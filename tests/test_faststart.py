"""An .m4a whose index is at the end is recognised, so a copy with the index first can be made."""
from routes.music import _moov_first, _slow_to_start


def box(kind, size):
    return size.to_bytes(4, 'big') + kind + b'\0' * (size - 8)


def test_index_first_and_last():
    assert _moov_first(box(b'ftyp', 32) + box(b'moov', 100) + box(b'mdat', 500)) is True
    assert _moov_first(box(b'ftyp', 32) + box(b'free', 16) + b'\0\0\0\x08mdat') is False
    assert _moov_first(box(b'ftyp', 32) + b'\0\0\0\x01mdat' + (10_000_000).to_bytes(8, 'big')) is False      # a 64-bit size


def test_unclear_or_broken_data_is_left_alone():
    assert _moov_first(b'') is None
    assert _moov_first(box(b'ftyp', 32)) is None
    assert _moov_first(b'\0\0\0\x02junk') is None


def test_which_songs_are_slow_to_start():
    ok_mp4 = box(b'ftyp', 32) + box(b'moov', 100) + box(b'mdat', 500)
    assert not _slow_to_start('m4a', ok_mp4)
    assert _slow_to_start('m4a', box(b'ftyp', 32) + b'\0\0\0\x08mdat')                        # index at the end
    assert _slow_to_start('m4a', box(b'ftyp', 32) + box(b'moov', 100) + b'covr')               # cover picture inside
    assert _slow_to_start('opus', b'OpusTags....METADATA_BLOCK_PICTURE=....')
    assert not _slow_to_start('opus', b'OpusTags....TITLE=x')
