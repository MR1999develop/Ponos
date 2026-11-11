# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A throwaway self-signed certificate and key for a local TLS server in the tests.

The key was made only for this purpose (`localhost`, 127.0.0.1) and protects nothing: no program
trusts this certificate, which is the point, since the tests need a server whose certificate does
not verify. Never use it for anything else.
"""

from __future__ import annotations

CERTIFICATE_PEM = """-----BEGIN CERTIFICATE-----
MIIBmzCCAUGgAwIBAgIUXwv4sOGWmJ1YkOnWhfq0ILx4KbkwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MTExMTEyMjEyMFoYDzIxMjYxMDE4
MTIyMTIwWjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAATcNfNQNcXZarQc6KlH0puzY8B4/1O4pTk40Oduh7yYKndTyEijCytF
y3z9Vod2K/puXRrV4Hs0d4copgj0kzNRo28wbTAdBgNVHQ4EFgQUeCj4e4ptj9EN
0zlxPzB0bSeCVicwHwYDVR0jBBgwFoAUeCj4e4ptj9EN0zlxPzB0bSeCVicwDwYD
VR0TAQH/BAUwAwEB/zAaBgNVHREEEzARgglsb2NhbGhvc3SHBH8AAAEwCgYIKoZI
zj0EAwIDSAAwRQIgBLLC4+Ne0hoU/li9L0tk82oX6oY2qbrMIEo6tVNttL4CIQCR
rGIdbguVHCg0OMEt0rJM+AWZHiDZn2nZWYHKiOhEWw==
-----END CERTIFICATE-----
"""

PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg4tr9RV6sLJGezFqc
OSalk9WkVHTiEHOdY32dJrIoPyuhRANCAATcNfNQNcXZarQc6KlH0puzY8B4/1O4
pTk40Oduh7yYKndTyEijCytFy3z9Vod2K/puXRrV4Hs0d4copgj0kzNR
-----END PRIVATE KEY-----
"""
