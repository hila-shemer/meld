# Copyright (C) 2002-2006 Stephen Kennedy <stevek@gnome.org>
# Copyright (C) 2011-2015 Kai Willadsen <kai.willadsen@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import bisect
import itertools
import logging
import os

from gi.module import get_introspection_module
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from meld.style import colour_lookup_with_fallback
from meld.treehelpers import SearchableTreeStore
from meld.vc._vc import (  # noqa: F401
    CONFLICT_BASE,
    CONFLICT_LOCAL,
    CONFLICT_MERGED,
    CONFLICT_OTHER,
    CONFLICT_REMOTE,
    CONFLICT_THIS,
    STATE_CONFLICT,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_IGNORED,
    STATE_MAX,
    STATE_MISSING,
    STATE_MODIFIED,
    STATE_NEW,
    STATE_NOCHANGE,
    STATE_NONE,
    STATE_NONEXIST,
    STATE_NORMAL,
    STATE_REMOVED,
    STATE_SPINNER,
)

log = logging.getLogger(__name__)

_GIGtk = None

try:
    _GIGtk = get_introspection_module("Gtk")
except Exception:
    log.warning("Unexpected error in introspection; folder comparisons may be slower")

(
    COL_PATH,
    COL_STATE,
    COL_TEXT,
    COL_ICON,
    COL_TINT,
    COL_FG,
    COL_STYLE,
    COL_WEIGHT,
    COL_STRIKE,
    COL_END,
) = list(range(10))

COL_TYPES = (str, str, str, str, Gdk.RGBA, Gdk.RGBA, Pango.Style, Pango.Weight, bool)


class DiffTreeStore(SearchableTreeStore):
    def __init__(self, ntree, types):
        full_types = []
        for col_type in COL_TYPES + tuple(types):
            full_types.extend([col_type] * ntree)
        super().__init__(*full_types)
        self._none_of_cols = {
            col_num: GObject.Value(col_type, None)
            for col_num, col_type in enumerate(full_types)
        }
        self.ntree = ntree
        self._diff_paths = None
        self._diff_paths_handlers = []
        self._setup_default_styles()

    def _setup_default_styles(self, style=None):
        roman, italic = Pango.Style.NORMAL, Pango.Style.ITALIC
        normal, bold = Pango.Weight.NORMAL, Pango.Weight.BOLD

        lookup = colour_lookup_with_fallback
        unk_fg = lookup("meld:unknown-text", "foreground")
        new_fg = lookup("meld:insert", "foreground")
        mod_fg = lookup("meld:replace", "foreground")
        del_fg = lookup("meld:delete", "foreground")
        err_fg = lookup("meld:error", "foreground")
        con_fg = lookup("meld:conflict", "foreground")

        self.text_attributes = [
            # foreground, style, weight, strikethrough
            (unk_fg, roman, normal, None),  # STATE_IGNORED
            (unk_fg, roman, normal, None),  # STATE_NONE
            (None, roman, normal, None),  # STATE_NORMAL
            (None, italic, normal, None),  # STATE_NOCHANGE
            (err_fg, roman, bold, None),  # STATE_ERROR
            (unk_fg, italic, normal, None),  # STATE_EMPTY
            (new_fg, roman, bold, None),  # STATE_NEW
            (mod_fg, roman, bold, None),  # STATE_MODIFIED
            (mod_fg, roman, normal, None),  # STATE_RENAMED
            (con_fg, roman, bold, None),  # STATE_CONFLICT
            (del_fg, roman, bold, True),  # STATE_REMOVED
            (del_fg, roman, bold, True),  # STATE_MISSING
            (unk_fg, roman, normal, True),  # STATE_NONEXIST
            (None, italic, normal, None),  # STATE_SPINNER
        ]

        self.icon_details = [
            # file-icon, folder-icon, file-tint
            ("text-x-generic", "folder", None),  # IGNORED
            ("text-x-generic", "folder", None),  # NONE
            ("text-x-generic", "folder", None),  # NORMAL
            ("text-x-generic", "folder", None),  # NOCHANGE
            ("dialog-warning-symbolic", None, None),  # ERROR
            (None, None, None),  # EMPTY
            ("text-x-generic", "folder", new_fg),  # NEW
            ("text-x-generic", "folder", mod_fg),  # MODIFIED
            ("text-x-generic", "folder", mod_fg),  # RENAMED
            ("text-x-generic", "folder", con_fg),  # CONFLICT
            ("text-x-generic", "folder", del_fg),  # REMOVED
            (None, "folder", unk_fg),  # MISSING
            (None, "folder", unk_fg),  # NONEXIST
            ("text-x-generic", "folder", None),  # SPINNER
        ]

        assert len(self.icon_details) == len(self.text_attributes) == STATE_MAX

    def iter_is_root(self, it: Gtk.TreeIter) -> bool:
        return self.iter_parent(it) is None

    def value_paths(self, it):
        return [self.value_path(it, i) for i in range(self.ntree)]

    def value_path(self, it, pane):
        return self.get_value(it, self.column_index(COL_PATH, pane))

    def is_folder(self, it, pane, path):
        # A folder may no longer exist, and is only tracked by VC.
        # Therefore, check the icon instead, as the pane already knows.
        icon = self.get_value(it, self.column_index(COL_ICON, pane))
        return icon == "folder" or (bool(path) and os.path.isdir(path))

    def column_index(self, col, pane):
        return self.ntree * col + pane

    def add_entries(self, parent, names):
        it = self.append(parent)
        for pane, path in enumerate(names):
            self.unsafe_set(it, pane, {COL_PATH: path})
        return it

    def add_empty(self, parent, text="empty folder"):
        it = self.append(parent)
        for pane in range(self.ntree):
            self.set_state(it, pane, STATE_EMPTY, text)
        return it

    def add_error(self, parent, msg, pane, defaults=None):
        if defaults is None:
            defaults = {}
        it = self.append(parent)
        key_values = {COL_STATE: str(STATE_ERROR)}
        key_values.update(defaults)
        for i in range(self.ntree):
            self.unsafe_set(it, i, key_values)
        self.set_state(it, pane, STATE_ERROR, msg)

    def set_path_state(self, it, pane, state, isdir=0, display_text=None):
        if not display_text:
            fullname = self.get_value(it, self.column_index(COL_PATH, pane))
            display_text = GLib.markup_escape_text(os.path.basename(fullname))
        self.set_state(it, pane, state, display_text, isdir)

    def set_state(self, it, pane, state, label, isdir=0):
        icon = self.icon_details[state][1 if isdir else 0]
        tint = None if isdir else self.icon_details[state][2]
        fg, style, weight, strike = self.text_attributes[state]
        self.unsafe_set(
            it,
            pane,
            {
                COL_STATE: str(state),
                COL_TEXT: label,
                COL_ICON: icon,
                COL_TINT: tint,
                COL_FG: fg,
                COL_STYLE: style,
                COL_WEIGHT: weight,
                COL_STRIKE: strike,
            },
        )

    def get_state(self, it, pane):
        state_idx = self.column_index(COL_STATE, pane)
        try:
            return int(self.get_value(it, state_idx))
        except TypeError:
            return None

    def _find_next_prev_diff(self, start_path):
        try:
            self.get_iter(start_path)
        except ValueError:
            # Invalid tree path
            return None, None

        # Tree order is the lexicographic order of path indices, so the
        # neighbouring differences are found by bisecting the sorted list.
        diff_paths = self._get_diff_paths()
        start = tuple(start_path.get_indices())
        before = bisect.bisect_left(diff_paths, start)
        after = bisect.bisect_right(diff_paths, start)
        prev_path = Gtk.TreePath(diff_paths[before - 1]) if before else None
        next_path = Gtk.TreePath(diff_paths[after]) if after < len(diff_paths) else None
        return prev_path, next_path

    def _get_diff_paths(self):
        """Get the paths of all differing rows, in tree order

        Walking a big tree from Python is slow enough to stall the UI, so
        the list is kept until the model next changes. The invalidation
        handlers are only connected while there is a list to invalidate,
        so that bulk updates such as a folder scan don't pay for them.
        """
        if self._diff_paths is not None:
            return self._diff_paths

        def match_func(it):
            # TODO: It works, but matching on the first pane only is very poor
            return self.get_state(it, 0) not in (
                STATE_NORMAL,
                STATE_NOCHANGE,
                STATE_EMPTY,
            )

        diff_paths = []
        root = self.get_iter_first()
        if root:
            for it in itertools.chain([root], self.inorder_search_down(root)):
                if match_func(it):
                    diff_paths.append(tuple(self.get_path(it).get_indices()))

        self._diff_paths = diff_paths
        self._diff_paths_handlers = [
            self.connect(signal, self._invalidate_diff_paths)
            for signal in (
                "row-changed",
                "row-deleted",
                "row-inserted",
                "rows-reordered",
            )
        ]
        return diff_paths

    def _invalidate_diff_paths(self, *args):
        self._diff_paths = None
        for handler_id in self._diff_paths_handlers:
            self.disconnect(handler_id)
        self._diff_paths_handlers = []

    def state_rows(self, states):
        """Generator of rows in one of the given states

        Tree iterators are returned in depth-first tree order.
        """
        root = self.get_iter_first()
        for it in self.inorder_search_down(root):
            state = self.get_state(it, 0)
            if state in states:
                yield it

    def unsafe_set(self, treeiter, pane, keys_values):
        """This must be fastest than super.set,
        at the cost that may crash the application if you don't
        know what your're passing here.
        ie: pass treeiter or column as None crash meld

        treeiter: Gtk.TreeIter
        keys_values: dict<column, value>
            column: Int col index
            value: Str (UTF-8), Int, Float, Double, Boolean, None or GObject

        return None
        """
        safe_keys_values = {
            self.column_index(col, pane): val
            if val is not None
            else self._none_of_cols.get(self.column_index(col, pane))
            for col, val in keys_values.items()
        }
        if _GIGtk and treeiter:
            columns = [col for col in safe_keys_values]
            values = [val for val in safe_keys_values.values()]
            _GIGtk.TreeStore.set(self, treeiter, columns, values)
        else:
            self.set(treeiter, safe_keys_values)


class MeldTreeView(Gtk.TreeView):
    __gtype_name__ = "MeldTreeView"

    context_menu_model = GObject.Property(
        type=Gio.MenuModel,
        flags=GObject.ParamFlags.READWRITE,
    )

    def __init__(self, *args, **kwargs):
        Gtk.TreeView.__init__(self, *args, **kwargs)

        controller = Gtk.GestureClick(
            button=3,
            propagation_phase=Gtk.PropagationPhase.CAPTURE,
        )
        controller.connect("pressed", self.on_treeview_button_press_event)
        self.add_controller(controller)
        self.set_search_equal_func(self.treeview_search_cb, None)

        # This is the only way I could get the context menu construction to behave
        # correctly. If the PopoverMenu wasn't constructed *and parented* during
        # __init__ then even though it appeared and functioned, the focus handling
        # was all wrong.
        self.context_menu = Gtk.PopoverMenu(
            position=Gtk.PositionType.BOTTOM,
            has_arrow=False,
            halign=Gtk.Align.START,
        )
        self.context_menu.set_parent(self)

    def do_realize(self):
        Gtk.TreeView.do_realize(self)
        self.context_menu.set_menu_model(self.context_menu_model)

    def do_size_allocate(self, *args):
        Gtk.TreeView.do_size_allocate(self, *args)
        self.context_menu.present()

    def on_treeview_button_press_event(self, controller, n_press, wx, wy):
        treeview = controller.get_widget()
        treeview.grab_focus()

        x, y = treeview.convert_widget_to_bin_window_coords(wx, wy)
        path = treeview.get_path_at_pos(int(x), int(y))
        if path is None:
            return False

        controller.set_state(Gtk.EventSequenceState.CLAIMED)
        selection = treeview.get_selection()
        model, rows = selection.get_selected_rows()

        row_paths = [str(r) for r in rows]
        if str(path[0]) not in row_paths:
            selection.unselect_all()
            selection.select_path(path[0])
            treeview.set_cursor(path[0])

        rect = Gdk.Rectangle()
        rect.x, rect.y = wx, wy

        treeview.context_menu.set_pointing_to(rect)
        treeview.context_menu.popup()
        return True

    def treeview_search_cb(self, model, column, key, it, data):
        # If the key contains a path separator, search the whole path,
        # otherwise just use the filename. If the key is all lower-case, do a
        # case-insensitive match.
        abs_search = "/" in key
        lower_key = key.islower()

        for path in model.value_paths(it):
            if not path:
                continue
            text = path if abs_search else os.path.basename(path)
            text = text.lower() if lower_key else text
            if key in text:
                return False
        return True
