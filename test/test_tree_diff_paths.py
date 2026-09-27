import random
from unittest import mock

import pytest


@pytest.fixture
def tree_module():
    from meld import tree

    # Row colours come from the style scheme, which tests don't install
    with mock.patch("meld.tree.colour_lookup_with_fallback", return_value=None):
        yield tree


def build_store(tree, seed):
    """A random single-pane tree with a mix of row states"""
    store = tree.DiffTreeStore(1, [])
    rng = random.Random(seed)
    states = [tree.STATE_NORMAL] * 6 + [
        tree.STATE_NOCHANGE,
        tree.STATE_EMPTY,
        tree.STATE_NEW,
        tree.STATE_MODIFIED,
    ]

    def fill(parent, depth):
        for _ in range(rng.randint(0, 4 if depth < 3 else 0)):
            it = store.append(parent)
            store.set_state(it, 0, rng.choice(states), "row")
            fill(it, depth + 1)

    root = store.append(None)
    store.set_state(root, 0, tree.STATE_NORMAL, "root")
    fill(root, 0)
    return store


def all_paths(store):
    paths = []
    store.foreach(lambda model, path, it: paths.append(path.copy()))
    return paths


def walk_next_prev(tree, store, path):
    """The reference answer: walk the tree from `path`"""

    def match_func(it):
        return store.get_state(it, 0) not in (
            tree.STATE_NORMAL,
            tree.STATE_NOCHANGE,
            tree.STATE_EMPTY,
        )

    return store.get_previous_next_paths(path, match_func)


@pytest.mark.parametrize("seed", range(5))
def test_next_prev_diff_matches_tree_walk(tree_module, seed):
    store = build_store(tree_module, seed)
    for path in all_paths(store):
        assert store._find_next_prev_diff(path) == walk_next_prev(
            tree_module, store, path
        )


def test_next_prev_diff_follows_model_changes(tree_module):
    store = build_store(tree_module, 1)
    paths = all_paths(store)
    first = paths[0]
    store._find_next_prev_diff(first)

    # Change every row to a difference; the cached answer must not survive
    for path in paths:
        store.set_state(store.get_iter(path), 0, tree_module.STATE_MODIFIED, "row")
    assert store._find_next_prev_diff(first) == walk_next_prev(
        tree_module, store, first
    )

    store.remove(store.get_iter(paths[-1]))
    for path in all_paths(store):
        assert store._find_next_prev_diff(path) == walk_next_prev(
            tree_module, store, path
        )


def test_next_prev_diff_invalid_path(tree_module):
    from gi.repository import Gtk

    store = build_store(tree_module, 2)
    assert store._find_next_prev_diff(Gtk.TreePath((5, 5, 5))) == (None, None)
