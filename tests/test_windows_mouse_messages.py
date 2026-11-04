"""Clasificación de mensajes Win32, sin necesidad de un host Windows."""
from gpia.backends.windows import mouse_button_for_message


def test_solo_pulsaciones_no_movimientos():
    assert mouse_button_for_message(0x0200) is None
    assert mouse_button_for_message(0x0201) == 1
    assert mouse_button_for_message(0x0202) is None
    assert mouse_button_for_message(0x0204) == 3
    assert mouse_button_for_message(0x0207) == 2
