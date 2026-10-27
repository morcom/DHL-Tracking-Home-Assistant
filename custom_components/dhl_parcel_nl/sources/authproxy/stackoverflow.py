#  SPDX-License-Identifier: CC-BY-SA-4.0
#  Vendored from authcaptureproxy 1.3.7 (code from Stack Overflow).
# ruff: noqa
"""
Python Package auth capture proxy.

This is code borrowed from stack overflow.
"""

import socket
from typing import Text


def get_open_port() -> int:
    """Get random open port.

    https://stackoverflow.com/questions/2838244/get-open-tcp-port-in-python/2838309#2838309

    Returns
        int: a random open port. This does not guarantee the port will remain open and may fail if there is a race condition.
    """
    s: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    s.listen(1)
    port: int = s.getsockname()[1]
    s.close()
    return port


def return_timer_countdown_refresh_html(
    seconds: int, text: Text, hard_refresh: bool = True
) -> Text:
    """Return JavaScript timer countdown for html injection. This is to use for tester success.

    https://stackoverflow.com/questions/16532577/javascript-refresh-countdown-text/16532611#16532611

    Args:
        seconds (int): Seconds to delay
        text (Text): HTML text to display before the timer text.
        hard_refresh (bool): Whether to force refresh of cache
    Returns:
        Text: HTML for injection
    """
    return f"""<html><head></head><body>{text}<div id="countdown"></div></body>
    <script defer="defer">(function countdown(remaining) {{
    if(remaining < 0)
        location.reload({"true" if hard_refresh else "false"});
    else{{
    document.getElementById('countdown').innerHTML = 'Automaticaly reloading page in ' + Math.max(Math.floor(remaining), 0) + ' seconds.';
    setTimeout(function(){{ countdown(remaining - 1); }}, 1000);
    }}}})({seconds});</script></html>"""
