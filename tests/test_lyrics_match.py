"""A lyrics search hit must be the song that is playing, not just a song of about the same length."""
from routes.music import _same_song, _like


def hit(track, artist, dur=152):
    return {'trackName': track, 'artistName': artist, 'duration': dur}


def test_same_song_with_small_differences():
    assert _same_song(hit('Love Letter', 'Lil Peep'), 'love letter', ['Lil Peep'], 152)
    assert _same_song(hit('love letter', "Lil' Peep"), 'Love Letter', ['Lil Peep'], 152)
    assert _same_song(hit('Star Shopping (Official Video)', 'Lil Peep'), 'Star Shopping', ['Lil Peep'], 152)
    assert _same_song(hit('Song', 'Artist A'), 'Song (feat. B)', ['Artist A', 'B'], 152)


def test_a_different_song_or_artist_is_rejected():
    assert not _same_song(hit('Love Letters', 'Ketty Lester'), 'love letter', ['Lil Peep'], 152)
    assert not _same_song(hit('Hellboy', 'Lil Peep'), 'love letter', ['Lil Peep'], 152)
    assert not _same_song(hit('Love Letter', 'Someone Else'), 'love letter', ['Lil Peep'], 152)


def test_original_script_entries_pass_on_an_almost_exact_length():
    assert _same_song(hit('スローモーションリプレイ', 'Aimer', 240), 'Slow Motion Replay', ['Aimer'], 241)
    assert not _same_song(hit('スローモーションリプレイ', 'Aimer', 250), 'Slow Motion Replay', ['Aimer'], 241)


def test_the_same_original_script_name_matches_by_name():
    assert _same_song(hit('ラストワン', 'BUMP OF CHICKEN', 276), 'ラストワン', ['BUMP OF CHICKEN'], 278)


def test_like_ignores_case_punctuation_and_brackets():
    assert _like('Don\'t Stop (Remastered)', 'dont stop', .72)
    assert not _like('abc', 'xyz', .72)
