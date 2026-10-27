# Vendored code

This folder contains code from **authcaptureproxy 1.3.7**
(https://github.com/alandtse/auth_capture_proxy), Copyright 2021 Alan Tse,
licensed under the Apache License 2.0 (see `LICENSE.txt`). `stackoverflow.py` is
CC-BY-SA-4.0 (from Stack Overflow, as in the original package).

It is vendored instead of installed because the package pins `multidict<7`, which
would downgrade the `multidict` 7.x shipped with Home Assistant 2026.10.

Changes: relative imports; the CLI, package metadata and example testers are left out;
no module level SSL context; `bs4` is imported lazily; debug logging never writes form
data (passwords), cookies, request/response headers or query strings (login codes);
the Amazon WAF host shims also cover amazon.nl, amazon.com.be, amazon.pl and amazon.se. `request.has_body` and
`asyncio.iscoroutinefunction` (deprecated) were replaced by `request.can_read_body` and
`inspect.iscoroutinefunction`.
