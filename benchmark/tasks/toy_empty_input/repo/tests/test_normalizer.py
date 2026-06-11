from normalizer import normalize


def test_blank_input():
    assert normalize("   ") == ""


def test_normal_text():
    assert normalize("  Hello   WORLD ") == "hello world"
