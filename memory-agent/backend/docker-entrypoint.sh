#!/bin/sh
set -eu

mkdir -p /data/chroma_data
exec "$@"

