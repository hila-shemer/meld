import os

import pytest


@pytest.fixture
def paths(tmp_path):
    (tmp_path / "file").write_text("x")
    (tmp_path / "dir").mkdir()
    os.symlink(tmp_path / "file", tmp_path / "link-to-file")
    os.symlink(tmp_path / "dir", tmp_path / "link-to-dir")
    os.symlink(tmp_path / "missing", tmp_path / "dangling")
    names = ["file", "dir", "link-to-file", "link-to-dir", "dangling", "missing"]
    return [str(tmp_path / name) for name in names]


def reference(func, path):
    try:
        return func(path)
    except OSError:
        return None


@pytest.mark.parametrize("prime_from_listing", [False, True])
def test_stat_cache_matches_os(paths, prime_from_listing):
    from meld.dirdiff import StatCache

    cache = StatCache()
    if prime_from_listing:
        # The scan seeds the cache with the lstat results from its listing
        for path in paths:
            cache.lstats[path] = reference(os.lstat, path)

    for path in paths:
        assert cache.lstat(path) == reference(os.lstat, path)
        assert cache.stat(path) == reference(os.stat, path)
        assert cache.exists(path) == os.path.exists(path)
        assert cache.isdir(path) == os.path.isdir(path)


def test_stat_or_raise_missing(paths):
    from meld.dirdiff import StatCache

    with pytest.raises(FileNotFoundError):
        StatCache().stat_or_raise(paths[-1])
