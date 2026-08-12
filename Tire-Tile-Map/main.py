
import sys
import os
import json
import traceback

import cv2
import numpy as np

from PySide6.QtCore import Qt, Signal, QSettings, QSize
from PySide6.QtGui import QImage, QPixmap, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QSlider,
    QCheckBox, QComboBox, QGroupBox, QFormLayout, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFileDialog, QMessageBox, QScrollArea, QSizePolicy,
    QSplitter, QFrame
)

# ---------------------------------------------------------------------------
# Application-wide constants
# ---------------------------------------------------------------------------
ORG_NAME = "InternalTools"
APP_NAME = "TireTileMapGenerator"
SUPPORTED_INPUT_FILTER = "Images (*.png *.jpg *.jpeg *.bmp)"
PREVIEW_MIN_SIZE = QSize(360, 360)


# =============================================================================
# SECTION 1 : IMAGE PROCESSING ENGINE
# =============================================================================
class TireProcessor:
    """
    Pure image-processing engine (no Qt dependency) that converts a
    tire tread image (BGR numpy array) into a clean black & white
    Tire Tile Map, following the pipeline described in the spec:

        Photographs:
            1. Grayscale
            2. CLAHE contrast enhancement
            3. Illumination correction
            4. Adaptive thresholding
            5. Morphological cleanup
            6. Contour extraction
            7. Contour filtering (remove small noise blobs)
            8. Fill detected tread blocks solid
            9. Smooth + generate clean tile map

        Technical Drawings:
            1. Grayscale
            2. Noise removal (blur)
            3. Adaptive thresholding
            4. Extract tread pattern
            5. Fill tread blocks
            6. Generate clean binary tile map

    All parameters are passed in through a single `params` dict so the
    UI layer can drive the pipeline live without the processor knowing
    anything about Qt widgets.
    """

    MODE_PHOTOGRAPH = "Photograph"
    MODE_TECHNICAL = "Technical Drawing"

    OUTPUT_TILE_MAP = "Tile Map"
    OUTPUT_BINARY_MASK = "Binary Mask"
    OUTPUT_HEIGHT_MAP = "Height Map"

    @staticmethod
    def _odd(value: int, minimum: int = 1) -> int:
        """Force a value to be odd and >= minimum (required by many
        OpenCV kernel / blur / adaptive-threshold parameters)."""
        value = max(int(value), minimum)
        if value % 2 == 0:
            value += 1
        return value

    # -------------------------------------------------------------------
    # Step-by-step pipeline stages (exposed individually so the UI can
    # optionally show intermediate debug output if desired later on).
    # -------------------------------------------------------------------

    @staticmethod
    def to_grayscale(img_bgr: np.ndarray) -> np.ndarray:
        """Step 1: Convert the source image to single-channel grayscale."""
        if len(img_bgr.shape) == 2:
            return img_bgr.copy()
        return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def denoise(gray: np.ndarray, blur_size: int) -> np.ndarray:
        """Step 2: Noise removal via Gaussian blur. Kernel is forced odd."""
        k = TireProcessor._odd(blur_size, minimum=1)
        if k <= 1:
            return gray.copy()
        return cv2.GaussianBlur(gray, (k, k), 0)

    @staticmethod
    def enhance_contrast_clahe(gray: np.ndarray) -> np.ndarray:
        """Step (photo only): CLAHE local contrast enhancement so tread
        detail is visible across uneven lighting."""
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        return clahe.apply(gray)

    @staticmethod
    def correct_illumination(gray: np.ndarray) -> np.ndarray:
        """Step (photo only): Illumination correction.

        Estimates a smooth "lighting" background via a large-radius
        Gaussian blur, then divides it out so the tread pattern has a
        flat, even brightness regardless of how the photo was lit.
        """
        background = cv2.GaussianBlur(gray, (0, 0), sigmaX=31, sigmaY=31)
        # Avoid divide-by-zero on pure black background pixels.
        background = np.where(background == 0, 1, background)
        normalized = cv2.divide(gray, background, scale=255)
        return normalized.astype(np.uint8)

    @staticmethod
    def adaptive_and_global_threshold(norm: np.ndarray, block_size: int,
                                       c_value: int, threshold: int) -> np.ndarray:
        """Step: Adaptive thresholding (local) combined with a manual
        global threshold (the "Threshold" slider) via a logical OR.

        Bright pixels (raised tread blocks catch more light / ink) are
        mapped to WHITE (255); dark recessed grooves are mapped to
        BLACK (0). Combining adaptive + global gives a pipeline that is
        robust across both evenly-lit technical scans and unevenly-lit
        real photographs.
        """
        block = TireProcessor._odd(block_size, minimum=3)

        adaptive = cv2.adaptiveThreshold(
            norm, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block,
            c_value
        )

        _, global_thresh = cv2.threshold(
            norm, threshold, 255, cv2.THRESH_BINARY
        )

        combined = cv2.bitwise_or(adaptive, global_thresh)
        return combined

    @staticmethod
    def morphological_cleanup(binary: np.ndarray, kernel_size: int) -> np.ndarray:
        """Step: Morphological close (fills small gaps inside tread
        blocks) followed by an open (removes isolated speckle noise)."""
        k = max(1, int(kernel_size))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
        opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel, iterations=1)
        return opened

    @staticmethod
    def extract_and_filter_contours(binary: np.ndarray, min_area: int) -> np.ndarray:
        """Step: Extract contours (tread blocks) and groove regions,
        discard anything below `min_area` as noise / isolated pixels,
        and fill genuinely tiny holes inside tread blocks solid.

        IMPORTANT: this intentionally uses connected-component
        analysis on BOTH polarities (white tread blobs and black
        groove blobs) rather than a naive `RETR_EXTERNAL` fill. A
        naive external-contour fill treats the entire connected tread
        network as one single blob and paints it solid, which wipes
        out every groove in the middle of the mask. Working on both
        polarities lets us clean sensor noise while correctly
        preserving real grooves as holes inside the tread network.
        """
        # 1) Remove tiny black specks (isolated "noise" pixels/holes
        #    inside tread blocks) by merging them into the surrounding
        #    white tread block when they are smaller than min_area.
        inverted = cv2.bitwise_not(binary)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
            inverted, connectivity=8
        )
        cleaned = binary.copy()
        for label_id in range(1, num_labels):  # skip background label 0
            area = stats[label_id, cv2.CC_STAT_AREA]
            if area < min_area:
                cleaned[labels == label_id] = 255

        # 2) Remove tiny white specks (isolated false-positive tread
        #    noise not part of the real tread pattern).
        num_labels2, labels2, stats2, _ = cv2.connectedComponentsWithStats(
            cleaned, connectivity=8
        )
        mask = np.zeros_like(binary)
        for label_id in range(1, num_labels2):
            area = stats2[label_id, cv2.CC_STAT_AREA]
            if area >= min_area:
                mask[labels2 == label_id] = 255

        return mask

    @staticmethod
    def smooth_edges(mask: np.ndarray) -> np.ndarray:
        """Step: Smooth jagged contour edges left over from thresholding
        by blurring then re-binarizing, producing clean artist-friendly
        silhouettes instead of pixel-stair-stepped edges."""
        blurred = cv2.GaussianBlur(mask, (5, 5), 0)
        _, smoothed = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY)
        return smoothed

    @staticmethod
    def generate_height_map(mask: np.ndarray) -> np.ndarray:
        """Bonus: Height Map Generation Mode.

        Uses a distance transform on the filled tread-block mask so
        that pixels deep inside a tread block are brighter (taller)
        and pixels near a groove edge fade toward black, producing a
        usable grayscale displacement/height map instead of a flat
        binary mask.
        """
        dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
        if dist.max() > 0:
            dist = (dist / dist.max()) * 255.0
        height_map = dist.astype(np.uint8)
        # Grooves (mask == 0) stay pure black.
        height_map = np.where(mask == 0, 0, height_map).astype(np.uint8)
        return height_map

    # -------------------------------------------------------------------
    # Full pipeline entry point
    # -------------------------------------------------------------------
    @classmethod
    def process(cls, img_bgr: np.ndarray, params: dict) -> np.ndarray:
        """
        Run the complete pipeline and return a single-channel uint8
        numpy array (the generated tile map / mask / height map,
        depending on `params['output_mode']`).

        Expected keys in `params`:
            mode            : cls.MODE_PHOTOGRAPH | cls.MODE_TECHNICAL
            output_mode     : cls.OUTPUT_TILE_MAP | OUTPUT_BINARY_MASK | OUTPUT_HEIGHT_MAP
            threshold       : int 0-255
            block_size      : int (forced odd, >=3)
            c_value         : int -50..50
            morph_kernel    : int >=1
            blur_size       : int (forced odd)
            min_area        : int >=0
            invert          : bool
        """
        gray = cls.to_grayscale(img_bgr)
        denoised = cls.denoise(gray, params.get("blur_size", 5))

        if params.get("mode", cls.MODE_PHOTOGRAPH) == cls.MODE_PHOTOGRAPH:
            enhanced = cls.enhance_contrast_clahe(denoised)
            normalized = cls.correct_illumination(enhanced)
        else:
            # Technical drawings are usually already flat/even, skip
            # CLAHE + illumination correction to avoid introducing
            # artifacts on clean line art.
            normalized = denoised

        binary = cls.adaptive_and_global_threshold(
            normalized,
            params.get("block_size", 21),
            params.get("c_value", 5),
            params.get("threshold", 127),
        )

        cleaned = cls.morphological_cleanup(binary, params.get("morph_kernel", 3))
        filled = cls.extract_and_filter_contours(cleaned, params.get("min_area", 50))
        smoothed = cls.smooth_edges(filled)

        output_mode = params.get("output_mode", cls.OUTPUT_TILE_MAP)
        if output_mode == cls.OUTPUT_HEIGHT_MAP:
            result = cls.generate_height_map(smoothed)
        elif output_mode == cls.OUTPUT_BINARY_MASK:
            # Raw, un-smoothed filled mask -> strictly binary 0/255.
            result = filled
        else:  # OUTPUT_TILE_MAP (default, smoothed clean output)
            result = smoothed

        if params.get("invert", False):
            result = cv2.bitwise_not(result)

        return result

    @staticmethod
    def make_before_after(original_bgr: np.ndarray, result_gray: np.ndarray) -> np.ndarray:
        """Bonus: Build a side-by-side Before/After comparison image
        (left = original, right = generated tile map), matched in
        height and separated by a thin divider line."""
        h, w = original_bgr.shape[:2]
        result_bgr = cv2.cvtColor(result_gray, cv2.COLOR_GRAY2BGR)
        result_bgr = cv2.resize(result_bgr, (w, h), interpolation=cv2.INTER_NEAREST)

        divider = np.full((h, 4, 3), 255, dtype=np.uint8)
        divider[:, :, :] = (0, 165, 255)  # orange divider line (BGR)

        combined = np.hstack([original_bgr, divider, result_bgr])
        return combined


# =============================================================================
# SECTION 2 : IMAGE <-> QPIXMAP HELPERS
# =============================================================================
def cv_to_qpixmap(img: np.ndarray) -> QPixmap:
    """Convert a numpy image (grayscale or BGR) into a QPixmap for
    display inside a QLabel."""
    if img is None:
        return QPixmap()

    if len(img.shape) == 2:
        h, w = img.shape
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
    else:
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = img_rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(img_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)

    # .copy() detaches the QImage from the numpy buffer's memory so the
    # pixmap stays valid after the numpy array is garbage collected.
    return QPixmap.fromImage(qimg.copy())


def imread_unicode(path: str):
    """cv2.imread wrapper that safely supports unicode / non-ASCII
    paths (common on Windows) by decoding through numpy's fromfile."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img
    except Exception:
        return None


def imwrite_unicode(path: str, img: np.ndarray) -> bool:
    """cv2.imwrite wrapper that safely supports unicode / non-ASCII
    paths by encoding through numpy's tofile."""
    try:
        ext = os.path.splitext(path)[1]
        if not ext:
            ext = ".png"
            path += ext
        result, encoded = cv2.imencode(ext, img)
        if not result:
            return False
        encoded.tofile(path)
        return True
    except Exception:
        return False


# =============================================================================
# SECTION 3 : DRAG & DROP ENABLED PREVIEW LABEL
# =============================================================================
class DropImageLabel(QLabel):
    """A QLabel that:
        - displays a scaled preview pixmap while preserving aspect ratio
        - accepts drag & drop of image files, emitting `fileDropped`
    """
    fileDropped = Signal(str)

    VALID_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")

    def __init__(self, placeholder_text: str, parent=None):
        super().__init__(parent)
        self._placeholder_text = placeholder_text
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(PREVIEW_MIN_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QLabel { background-color: #202225; color: #8a8f98; "
            "border: 2px dashed #3a3d42; border-radius: 6px; font-size: 13px; }"
        )
        self.setText(self._placeholder_text)

    def set_image(self, img: np.ndarray):
        """Set the preview image from a numpy array (BGR or grayscale)."""
        if img is None:
            self.clear_image()
            return
        self._source_pixmap = cv_to_qpixmap(img)
        self.setStyleSheet(
            "QLabel { background-color: #101113; border: 1px solid #3a3d42; border-radius: 6px; }"
        )
        self._rescale()

    def clear_image(self):
        self._source_pixmap = QPixmap()
        self.setText(self._placeholder_text)
        self.setStyleSheet(
            "QLabel { background-color: #202225; color: #8a8f98; "
            "border: 2px dashed #3a3d42; border-radius: 6px; font-size: 13px; }"
        )

    def _rescale(self):
        if self._source_pixmap.isNull():
            return
        scaled = self._source_pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.setPixmap(scaled)

    def resizeEvent(self, event):
        self._rescale()
        super().resizeEvent(event)

    # -- Drag & Drop -----------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(self.VALID_EXTENSIONS):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self.VALID_EXTENSIONS):
                self.fileDropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()


# =============================================================================
# SECTION 4 : LABELED SLIDER WIDGET (reusable control row)
# =============================================================================
class LabeledSlider(QWidget):
    """A single-row, compact control: title on the left, slider in the
    middle, live numeric value on the right — kept to one line so a
    page of controls stays easy to scan."""
    valueChanged = Signal(int)

    def __init__(self, title: str, min_val: int, max_val: int, default: int,
                 step: int = 1, parent=None):
        super().__init__(parent)
        self._title = title

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #d0d2d6; font-size: 12px;")
        self.title_label.setFixedWidth(130)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(min_val)
        self.slider.setMaximum(max_val)
        self.slider.setSingleStep(step)
        self.slider.setValue(default)
        self.slider.valueChanged.connect(self._on_value_changed)

        self.value_label = QLabel(str(default))
        self.value_label.setStyleSheet("color: #6fb2ff; font-size: 12px; font-weight: 600;")
        self.value_label.setFixedWidth(32)
        self.value_label.setAlignment(Qt.AlignRight)

        layout.addWidget(self.title_label)
        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.value_label)

    def _on_value_changed(self, value: int):
        self.value_label.setText(str(value))
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int):
        self.slider.setValue(value)
        self.value_label.setText(str(value))


# =============================================================================
# SECTION 5 : MAIN APPLICATION WINDOW
# =============================================================================
class MainWindow(QMainWindow):
    """Main desktop window: wires the UI together with the
    TireProcessor engine and handles all user interaction."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tire Tile Map Generator")
        self.resize(1360, 860)

        # -- State -------------------------------------------------------
        self.original_bgr = None       # currently loaded source image (numpy, BGR)
        self.output_mask = None        # last generated result (numpy, grayscale)
        self.current_file_path = None  # path of the currently loaded image

        self.settings = QSettings(ORG_NAME, APP_NAME)

        self._build_ui()
        self._connect_signals()
        self._load_last_settings()
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # UI CONSTRUCTION
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(splitter)

        # ---------------- LEFT : Preview area ----------------
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        previews_row = QHBoxLayout()

        original_box = QGroupBox("Original Image Preview")
        original_layout = QVBoxLayout(original_box)
        self.original_label = DropImageLabel("Drag & drop an image here\nor click 'Load Image'")
        original_layout.addWidget(self.original_label)

        output_box = QGroupBox("Output Tile Map Preview")
        output_layout = QVBoxLayout(output_box)
        self.output_label = DropImageLabel("Generated tile map will appear here")
        output_layout.addWidget(self.output_label)

        previews_row.addWidget(original_box)
        previews_row.addWidget(output_box)
        preview_layout.addLayout(previews_row)

        # -- Primary action buttons row --
        actions_row = QHBoxLayout()
        self.btn_load = QPushButton("Load Image")
        self.btn_generate = QPushButton("Generate Tile Map")
        self.btn_save = QPushButton("Save PNG")
        self.btn_reset = QPushButton("Reset")
        for b in (self.btn_load, self.btn_generate, self.btn_save, self.btn_reset):
            b.setMinimumHeight(34)
            actions_row.addWidget(b)
        preview_layout.addLayout(actions_row)

        self.status_label = QLabel("Ready. Load a tire image to begin.")
        self.status_label.setStyleSheet("color: #8a8f98; font-size: 11px; padding-top: 4px;")
        preview_layout.addWidget(self.status_label)

        splitter.addWidget(preview_widget)

        # ---------------- RIGHT : Controls sidebar ----------------
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFixedWidth(340)

        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setSpacing(12)

        # -- Mode selection --
        mode_box = QGroupBox("Output Mode")
        mode_layout = QFormLayout(mode_box)

        self.combo_output_mode = QComboBox()
        self.combo_output_mode.addItems([
            TireProcessor.OUTPUT_TILE_MAP,
            TireProcessor.OUTPUT_BINARY_MASK,
        ])
        mode_layout.addRow("Output Mode:", self.combo_output_mode)
        controls_layout.addWidget(mode_box)

        # -- Sliders --
        sliders_box = QGroupBox("Controls")
        sliders_layout = QVBoxLayout(sliders_box)

        self.slider_threshold = LabeledSlider("Threshold", 0, 255, 255)
        self.slider_block_size = LabeledSlider("Adaptive Block Size", 3, 99, 99)
        self.slider_c_value = LabeledSlider("Adaptive C Value", -30, 30, 5)
        self.slider_morph_kernel = LabeledSlider("Morphology Kernel Size", 1, 25, 3)
        self.slider_blur_size = LabeledSlider("Blur Size", 1, 25, 3)
        self.slider_min_area = LabeledSlider("Minimum Contour Area", 0, 2000, 45)

        for s in (self.slider_threshold, self.slider_block_size, self.slider_c_value,
                  self.slider_morph_kernel, self.slider_blur_size, self.slider_min_area):
            sliders_layout.addWidget(s)

        controls_layout.addWidget(sliders_box)

        # -- Checkboxes --
        options_box = QGroupBox("Options")
        options_layout = QVBoxLayout(options_box)
        self.chk_invert = QCheckBox("Invert Output")
        options_layout.addWidget(self.chk_invert)
        controls_layout.addWidget(options_box)

        controls_layout.addStretch()

        controls_scroll.setWidget(controls_container)
        splitter.addWidget(controls_scroll)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

    # ------------------------------------------------------------------
    # SIGNAL WIRING
    # ------------------------------------------------------------------
    def _connect_signals(self):
        self.btn_load.clicked.connect(self.load_image)
        self.btn_generate.clicked.connect(self.generate_tile_map)
        self.btn_save.clicked.connect(self.save_png)
        self.btn_reset.clicked.connect(self.reset_all)

        self.original_label.fileDropped.connect(self._load_image_from_path)

        # Auto-update preview whenever any control changes, if enabled.
        for s in (self.slider_threshold, self.slider_block_size, self.slider_c_value,
                  self.slider_morph_kernel, self.slider_blur_size, self.slider_min_area):
            s.valueChanged.connect(self._maybe_auto_update)

        self.chk_invert.stateChanged.connect(self._maybe_auto_update)
        self.combo_output_mode.currentTextChanged.connect(self._maybe_auto_update)

    # ------------------------------------------------------------------
    # CORE ACTIONS
    # ------------------------------------------------------------------
    def load_image(self):
        """Open a file dialog and load the chosen tire image."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Tire Image", "", SUPPORTED_INPUT_FILTER
        )
        if path:
            self._load_image_from_path(path)

    def _load_image_from_path(self, path: str):
        img = imread_unicode(path)
        if img is None:
            QMessageBox.warning(self, "Load Failed",
                                 f"Could not read image:\n{path}")
            return

        self.original_bgr = img
        self.current_file_path = path
        self.output_mask = None
        self.original_label.set_image(img)
        self.output_label.clear_image()
        self.status_label.setText(f"Loaded: {os.path.basename(path)}  "
                                   f"({img.shape[1]}x{img.shape[0]})")

        if self.original_bgr is not None:
            self.generate_tile_map()

    def _current_params(self) -> dict:
        """Collect current slider/checkbox state into the params dict
        consumed by TireProcessor.process()."""
        return {
            "mode": TireProcessor.MODE_PHOTOGRAPH,
            "output_mode": self.combo_output_mode.currentText(),
            "threshold": self.slider_threshold.value(),
            "block_size": self.slider_block_size.value(),
            "c_value": self.slider_c_value.value(),
            "morph_kernel": self.slider_morph_kernel.value(),
            "blur_size": self.slider_blur_size.value(),
            "min_area": self.slider_min_area.value(),
            "invert": self.chk_invert.isChecked(),
        }

    def generate_tile_map(self):
        """Run the full processing pipeline on the loaded image and
        update the output preview."""
        if self.original_bgr is None:
            QMessageBox.information(self, "No Image", "Please load a tire image first.")
            return

        try:
            params = self._current_params()
            result = TireProcessor.process(self.original_bgr, params)
            self.output_mask = result
            self.output_label.set_image(result)

            self.status_label.setText("Tile map generated successfully.")
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "Processing Error", str(exc))

    def _maybe_auto_update(self, *args):
        """Live-updates the output preview whenever a control changes.
        Auto-update is always on (no toggle) for immediate feedback."""
        if self.original_bgr is not None:
            self.generate_tile_map()

    def save_png(self):
        """Save the last generated tile map to a PNG chosen by the user."""
        if self.output_mask is None:
            QMessageBox.information(self, "Nothing to Save",
                                     "Generate a tile map before saving.")
            return

        default_name = "tire_tile_map.png"
        if self.current_file_path:
            base = os.path.splitext(os.path.basename(self.current_file_path))[0]
            default_name = f"{base}_tile_map.png"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Tile Map As", default_name, "PNG Image (*.png)"
        )
        if not path:
            return

        if not path.lower().endswith(".png"):
            path += ".png"

        ok = imwrite_unicode(path, self.output_mask)
        if ok:
            self.status_label.setText(f"Saved: {path}")
            QMessageBox.information(self, "Saved", f"Tile map saved to:\n{path}")
        else:
            QMessageBox.critical(self, "Save Failed", "Could not write PNG file.")

    def reset_all(self):
        """Reset all advanced controls to their defaults and clear the
        generated output (the loaded source image is kept)."""
        self.slider_threshold.set_value(255)
        self.slider_block_size.set_value(99)
        self.slider_c_value.set_value(5)
        self.slider_morph_kernel.set_value(3)
        self.slider_blur_size.set_value(3)
        self.slider_min_area.set_value(45)
        self.chk_invert.setChecked(False)
        self.combo_output_mode.setCurrentIndex(0)

        self.output_mask = None
        self.output_label.clear_image()
        self.status_label.setText("Controls reset to defaults.")

    def _apply_params(self, data: dict):
        """Apply a params dict (from QSettings) to the UI controls."""
        if "output_mode" in data:
            idx = self.combo_output_mode.findText(data["output_mode"])
            if idx >= 0:
                self.combo_output_mode.setCurrentIndex(idx)

        self.slider_threshold.set_value(int(data.get("threshold", 255)))
        self.slider_block_size.set_value(int(data.get("block_size", 99)))
        self.slider_c_value.set_value(int(data.get("c_value", 5)))
        self.slider_morph_kernel.set_value(int(data.get("morph_kernel", 3)))
        self.slider_blur_size.set_value(int(data.get("blur_size", 3)))
        self.slider_min_area.set_value(int(data.get("min_area", 45)))
        self.chk_invert.setChecked(bool(data.get("invert", False)))

    # ------------------------------------------------------------------
    # BONUS : REMEMBER LAST USED SETTINGS (QSettings, persisted on disk)
    # ------------------------------------------------------------------
    def _load_last_settings(self):
        raw = self.settings.value("last_params", "")
        if raw:
            try:
                data = json.loads(raw)
                self._apply_params(data)
            except Exception:
                pass  # Corrupt / outdated settings blob -> silently ignore.

    def _save_last_settings(self):
        self.settings.setValue("last_params", json.dumps(self._current_params()))

    def closeEvent(self, event):
        """Persist the current control state so it is restored the next
        time the application starts."""
        self._save_last_settings()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # DRAG & DROP directly onto the main window (in addition to the
    # dedicated preview label) for convenience.
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                self._load_image_from_path(path)
                event.acceptProposedAction()
                return
        event.ignore()


# =============================================================================
# SECTION 6 : ENTRY POINT
# =============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Simple dark palette applied via stylesheet for an artist-friendly,
    # low eye-strain tool UI.
    app.setStyleSheet("""
        QMainWindow { background-color: #17181a; }
        QWidget { background-color: #17181a; color: #e6e6e6; font-family: 'Segoe UI', sans-serif; }
        QGroupBox {
            border: 1px solid #33363a; border-radius: 6px; margin-top: 10px;
            padding-top: 10px; font-weight: 600; color: #cfd2d6;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QPushButton {
            background-color: #2b6de0; color: white; border: none;
            border-radius: 5px; padding: 6px 12px; font-weight: 600;
        }
        QPushButton:hover { background-color: #3b7cf0; }
        QPushButton:pressed { background-color: #1f57bd; }
        QComboBox, QCheckBox { padding: 3px; }
        QSlider::groove:horizontal { height: 4px; background: #33363a; border-radius: 2px; }
        QSlider::handle:horizontal {
            background: #2b6de0; width: 14px; margin: -6px 0; border-radius: 7px;
        }
        QScrollArea { border: none; }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()