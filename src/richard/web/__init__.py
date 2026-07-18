"""Web configuration UI for Richard.

A small dependency-free async HTTP server that hosts a JSON API and a single-page
config UI, run alongside the satellite relay server (`richard serve`). It lets you
inspect and edit Richard's config, view discovered devices, manage memories, and see
connected satellites, control loops, and generated event notifications from a browser.
"""

from richard.web.app import Response, WebApp, serve_web

__all__ = ["Response", "WebApp", "serve_web"]
