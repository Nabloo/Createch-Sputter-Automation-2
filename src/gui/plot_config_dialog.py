"""Configuration dialog for plot channels, colours, and history window.

Channels are grouped by unit so the user can only select channels with
the same unit in a single plot (e.g. rate channels in one plot,
thickness channels in another).  When a channel in a different unit
group is selected, all previously-selected channels are deselected.
"""

import logging
from collections import OrderedDict
from typing import Dict, List, Optional

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.gui.plot_widget import _channel_legend_name

logger = logging.getLogger(__name__)

_COLOUR_PRESETS = [
    "#00bfff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#20c997", "#f06595",
    "#748ffc", "#94d82d", "#ff8787", "#4dabf7",
]


class PlotConfigDialog(QDialog):
    """Dialog for configuring plot channels, colours, and history window.

    When *channel_units* is provided, channels are grouped by unit and
    selecting a channel in one group auto-deselects all channels in
    different groups, ensuring only same-unit channels are plotted together.
    """

    def __init__(
        self,
        channels: List[str],
        colours: Optional[Dict[str, str]] = None,
        visibility: Optional[Dict[str, bool]] = None,
        channel_units: Optional[Dict[str, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Plot Channels")
        self.setMinimumWidth(420)

        self._channels = list(channels)
        self._colours = dict(colours) if colours else {}
        self._visibility = dict(visibility) if visibility else {}
        self._channel_units = dict(channel_units) if channel_units else {}

        # Group channels by unit for display
        # OrderedDict preserves insertion order for stable display
        self._unit_groups: "OrderedDict[str, List[str]]" = OrderedDict()
        self._build_unit_groups()

        self._checkboxes: Dict[str, QCheckBox] = {}
        self._colour_buttons: Dict[str, QPushButton] = {}

        self._build_ui()
        self._populate()
        # If the initial visibility state spans multiple unit groups (e.g. from an
        # old config saved before unit-grouping was introduced), collapse to the
        # first group so the dialog starts in a consistent single-group selection.
        self._ensure_single_unit_group()

    # ------------------------------------------------------------------
    # Unit grouping
    # ------------------------------------------------------------------

    def _build_unit_groups(self) -> None:
        """Partition *self._channels* by unit, preserving channel order."""
        groups: "OrderedDict[str, List[str]]" = OrderedDict()
        has_units = any(self._channel_units.get(ch, "") for ch in self._channels)

        if not has_units:
            # No unit info — put everything in a single "Channels" group
            groups[""] = list(self._channels)
            self._unit_groups = groups
            return

        # Collect groups in order of first appearance
        seen_units: List[str] = []
        for ch in self._channels:
            u = self._channel_units.get(ch, "")
            if u not in seen_units:
                seen_units.append(u)
                groups[u] = []
            groups[u].append(ch)

        self._unit_groups = groups

    def _unit_label(self, unit: str) -> str:
        """Return a human-readable label for a unit group."""
        return unit if unit else "Other"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def selected_channels(self) -> List[str]:
        return [ch for ch, cb in self._checkboxes.items() if cb.isChecked()]

    @property
    def selected_colours(self) -> List[str]:
        return [
            self._colours.get(ch, _COLOUR_PRESETS[
                list(self._checkboxes.keys()).index(ch) % len(_COLOUR_PRESETS)
            ])
            for ch in self.selected_channels
        ]

    @property
    def channel_visibility(self) -> Dict[str, bool]:
        return {ch: cb.isChecked() for ch, cb in self._checkboxes.items()}

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        channels_group = QGroupBox("Channels")
        channels_layout = QVBoxLayout(channels_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._channel_container = QWidget()
        self._channel_form = QFormLayout(self._channel_container)
        self._channel_form.setContentsMargins(0, 0, 0, 0)
        self._channel_form.setSpacing(4)
        scroll.setWidget(self._channel_container)

        channels_layout.addWidget(scroll)
        layout.addWidget(channels_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _populate(self) -> None:
        ch_index = 0
        for unit, group_channels in self._unit_groups.items():
            # Section header for the unit group
            label_text = self._unit_label(unit)
            # Add a separator line above non-first groups
            if unit not in ("", "") and list(self._unit_groups.keys())[0] != unit:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setFrameShadow(QFrame.Sunken)
                sep.setStyleSheet("color: #555;")
                self._channel_form.addRow(sep)

            header = QLabel(label_text)
            header.setStyleSheet(
                "font-weight: bold; font-size: 13px; color: #aaa; padding: 4px 0 2px 0;"
            )
            self._channel_form.addRow(header)

            for ch in group_channels:
                visible = self._visibility.get(ch, True)
                colour = self._colours.get(ch, _COLOUR_PRESETS[ch_index % len(_COLOUR_PRESETS)])
                ch_index += 1

                display = _channel_legend_name(ch)

                cb = QCheckBox(display)
                cb.setChecked(visible)
                cb.toggled.connect(lambda checked, c=ch: self._on_channel_toggled(c, checked))
                self._checkboxes[ch] = cb

                btn = QPushButton()
                btn.setFixedSize(32, 20)
                btn.setStyleSheet(
                    f"background-color: {colour}; border: 1px solid #555; "
                    f"border-radius: 3px;"
                )
                btn.setToolTip(f"Click to change colour for {display}")
                btn.clicked.connect(lambda checked, c=ch: self._pick_colour(c))
                self._colour_buttons[ch] = btn

                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                row.addWidget(cb)
                row.addWidget(btn)
                row.addStretch()

                row_widget = QWidget()
                row_widget.setLayout(row)
                # Indent channels under their unit header
                self._channel_form.addRow(row_widget)
                self._channel_form.itemAt(
                    self._channel_form.count() - 1
                ).widget().setStyleSheet("padding-left: 16px;")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _ensure_single_unit_group(self) -> None:
        """Collapse selection to a single unit group if multiple are checked.

        Called on dialog initialisation to handle old configs where channels
        from different unit groups may have been saved together (before
        unit-grouping was introduced).  Keeps the first unit group (by
        display order) that has at least one checked channel.
        """
        if not self._channel_units or len(self._unit_groups) < 2:
            return
        groups_with_selection = set()
        for ch in self.selected_channels:
            groups_with_selection.add(self._channel_units.get(ch, ""))
        if len(groups_with_selection) <= 1:
            return
        # Find the first group (by insertion order) that has any checked channel
        target_channels: Optional[List[str]] = None
        for group_chs in self._unit_groups.values():
            if any(cb.isChecked() for ch, cb in self._checkboxes.items() if ch in group_chs):
                target_channels = group_chs
                break
        if target_channels is None:
            return
        # Keep only channels from the chosen group
        for ch, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(ch in target_channels)
            cb.blockSignals(False)

    def _on_channel_toggled(self, channel: str, checked: bool) -> None:
        """When a channel is toggled, only toggle the clicked channel.

        If the clicked channel belongs to a different unit group than
        any currently-checked channels, those other-group channels are
        unchecked to prevent mixing units.  Same-group channels are
        left as-is.
        """
        if not checked:
            return  # unchecking a single channel is always allowed

        # Only uncheck channels from *other* unit groups — don't
        # auto-check all same-group channels.
        target_unit = self._channel_units.get(channel, "")
        for ch, cb in self._checkboxes.items():
            if ch == channel:
                continue
            unit = self._channel_units.get(ch, "")
            if unit != target_unit and cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)

    def _pick_colour(self, channel: str) -> None:
        current = QColor(self._colours.get(channel, _COLOUR_PRESETS[0]))
        colour = QColorDialog.getColor(current, self, f"Colour for {_channel_legend_name(channel)}")
        if colour.isValid():
            hex_str = colour.name()
            self._colours[channel] = hex_str
            btn = self._colour_buttons.get(channel)
            if btn:
                btn.setStyleSheet(
                    f"background-color: {hex_str}; border: 1px solid #555; "
                    f"border-radius: 3px;"
                )
