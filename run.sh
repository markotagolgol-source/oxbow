#!/bin/bash
# Start Oxbow. Pass a PDF path to open that file.
cd "$(dirname "$0")"
if [ "$#" -eq 1 ] && [ -z "$1" ]; then
    set --
fi
exec python3 -m oxbow "$@"
