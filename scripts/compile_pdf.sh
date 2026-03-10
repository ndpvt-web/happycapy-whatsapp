#!/usr/bin/env bash
# compile_pdf.sh - Compile LaTeX to PDF with proper environment setup
# Handles broken TeX Live installations (missing ls-R database) by
# setting explicit search paths for all TeX resources.
#
# Usage: compile_pdf.sh <input.tex> <output.pdf>

set -euo pipefail

INPUT_TEX="${1:?Usage: compile_pdf.sh <input.tex> <output.pdf>}"
OUTPUT_PDF="${2:?Usage: compile_pdf.sh <input.tex> <output.pdf>}"

TEXLIVE_DIST="/usr/share/texlive/texmf-dist"

# Only apply env overrides if kpsewhich is broken (ls-R missing)
if ! kpsewhich article.cls &>/dev/null 2>&1; then
    export TEXINPUTS=".:${TEXLIVE_DIST}/tex//:"
    export TFMFONTS="${TEXLIVE_DIST}/fonts/tfm//:"
    export T1FONTS="${TEXLIVE_DIST}/fonts/type1//:"
    export VFFONTS="${TEXLIVE_DIST}/fonts/vf//:"
    export ENCFONTS="${TEXLIVE_DIST}/fonts/enc//:"
    export TEXFONTMAPS="${TEXLIVE_DIST}/fonts/map//:"
    export TEXPSHEADERS="${TEXLIVE_DIST}/fonts/enc//:"
    export BIBINPUTS=".:${TEXLIVE_DIST}/bibtex/bib//:"
    export BSTINPUTS=".:${TEXLIVE_DIST}/bibtex/bst//:"
    export VARTEXFONTS="${HOME}/texmf-var/fonts"
    export MFINPUTS=".:${TEXLIVE_DIST}/metafont//:"
    export MFBASES="${HOME}/.texlive2022/texmf-var/web2c/metafont//:"
    mkdir -p "${HOME}/texmf-var/fonts"
fi

# Work in the directory containing the tex file
WORK_DIR="$(dirname "$(realpath "$INPUT_TEX")")"
TEX_FILE="$(basename "$INPUT_TEX")"
TEX_BASE="${TEX_FILE%.tex}"

cd "$WORK_DIR"

# Detect engine from source
ENGINE="pdflatex"
if grep -qE '\\usepackage\{(fontspec|xeCJK|polyglossia)\}' "$TEX_FILE" 2>/dev/null; then
    ENGINE="xelatex"
elif grep -qE '\\usepackage\{(luacode|luatextra)\}|\\directlua' "$TEX_FILE" 2>/dev/null; then
    ENGINE="lualatex"
fi

# Two-pass compilation for references/TOC
COMPILE_CMD=("$ENGINE" "-interaction=nonstopmode" "-halt-on-error" "$TEX_FILE")
PASS1_OK=false

for pass in 1 2; do
    if "${COMPILE_CMD[@]}" > /dev/null 2>&1; then
        PASS1_OK=true
    elif [[ $pass -eq 1 ]]; then
        # First pass failed - show error and exit
        "${COMPILE_CMD[@]}" 2>&1 | tail -20 >&2
        exit 1
    fi
done

PDF_FILE="${WORK_DIR}/${TEX_BASE}.pdf"
if [[ -f "$PDF_FILE" ]]; then
    cp "$PDF_FILE" "$OUTPUT_PDF"
    exit 0
else
    echo "Error: No PDF output produced" >&2
    exit 1
fi
