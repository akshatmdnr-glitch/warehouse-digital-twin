#!/usr/bin/env bash
# Render architecture diagrams with Graphviz.
#
#   scripts/generate_diagrams.sh
#
# Produces SVG + PNG + PDF for every docs/diagrams/*.dot.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/docs/diagrams"

command -v dot >/dev/null 2>&1 || { echo "graphviz (dot) not installed"; exit 1; }

for f in "$DIR"/*.dot; do
  name="$(basename "$f" .dot)"
  dot -Tsvg "$f" -o "$DIR/$name.svg"
  dot -Tpng "$f" -o "$DIR/$name.png"
  dot -Tpdf "$f" -o "$DIR/$name.pdf"
  echo "rendered $name (svg/png/pdf)"
done
