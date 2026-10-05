from ui import DEMO_GUIDES, DEMO_NAMES, demo_guide


def test_each_demo_has_a_short_dynamic_guide():
    assert DEMO_GUIDES.keys() == DEMO_NAMES.keys()
    for name in DEMO_NAMES:
        guide = demo_guide(name)
        assert "<ol>" in guide
        assert guide.count("<li>") == 3
        assert "GLiNER" in guide
        assert "JPT" in demo_guide(name, "jpt")
