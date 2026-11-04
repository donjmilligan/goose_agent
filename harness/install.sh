#!/usr/bin/env bash
# Install the agent harness into goose's own discovery directories.
#
# Every target below was read from goose source; see harness/README.md
# "Extension points" for the file:line references. Two facts shape the design:
#
#   * goose opens agent, recipe and check files with O_NOFOLLOW
#     (crates/goose/src/skills/supporting_files.rs read_confined_file_with_hook,
#     crates/goose/src/checks/mod.rs read_checks_dir), so a per-file symlink is
#     skipped. Those are installed as managed COPIES.
#   * a global hints file, and anything it @-includes, must canonicalize inside
#     the goose config dir (crates/goose/src/hints/import_files.rs
#     sanitize_existing_path). So the working agreement is COPIED into the
#     config dir and @-included from a marked block in the global .goosehints.
#
# The plugin directory is read with plain std::fs, so it IS symlinked, which
# keeps hooks and MCP servers live against the harness tree.
#
# Ownership: everything this script creates is recorded in a manifest with a
# checksum. It never overwrites or removes a file it did not create, nor one
# that was edited after it wrote it; it skips those with a warning.

set -euo pipefail

PLUGIN_NAME="agent-harness"
AGREEMENT_COPY_NAME="agent-harness-working-agreement.md"
BLOCK_BEGIN="# >>> agent-harness (managed by harness/install.sh; remove with --uninstall) >>>"
BLOCK_END="# <<< agent-harness <<<"
# shellcheck disable=SC2016 # the literal placeholder, not an expansion
TEMPLATE_TOKEN='${HARNESS_ROOT}'

DRY_RUN=0
UNINSTALL=0
HARNESS_ROOT=""
WARNINGS=0
CHANGES=0

usage() {
    cat <<'EOF'
Usage: harness/install.sh [--dry-run] [--uninstall] [--harness-root DIR]

  --dry-run           print what would change; touch nothing
  --uninstall         remove only what this script installed
  --harness-root DIR  harness tree to install (default: this script's directory)

Respects HOME, XDG_CONFIG_HOME, XDG_STATE_HOME and GOOSE_PATH_ROOT the same
way goose does, so it can be sandboxed with a temporary HOME.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --harness-root)
            [ $# -ge 2 ] || { echo "error: --harness-root needs a directory" >&2; exit 2; }
            HARNESS_ROOT="$2"
            shift
            ;;
        --harness-root=*) HARNESS_ROOT="${1#--harness-root=}" ;;
        -h | --help) usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ -z "$HARNESS_ROOT" ]; then
    HARNESS_ROOT="$(dirname "$0")"
fi
[ -d "$HARNESS_ROOT" ] || { echo "error: harness root not found: $HARNESS_ROOT" >&2; exit 2; }
HARNESS_ROOT="$(cd "$HARNESS_ROOT" && pwd -P)"

: "${HOME:?HOME must be set}"

is_absolute() { case "$1" in /*) return 0 ;; *) return 1 ;; esac; }

# Mirrors crates/goose/src/config/paths.rs: GOOSE_PATH_ROOT (absolute only)
# wins; otherwise etcetera's Xdg strategy, which goose uses on macOS and Linux.
GOOSE_ROOT="${GOOSE_PATH_ROOT:-}"
if [ -n "$GOOSE_ROOT" ] && is_absolute "$GOOSE_ROOT"; then
    CONFIG_DIR="$GOOSE_ROOT/config"
    PLUGINS_DIR="$GOOSE_ROOT/.agents/plugins"
else
    if [ -n "${XDG_CONFIG_HOME:-}" ] && is_absolute "$XDG_CONFIG_HOME"; then
        CONFIG_DIR="$XDG_CONFIG_HOME/goose"
    else
        CONFIG_DIR="$HOME/.config/goose"
    fi
    PLUGINS_DIR="$HOME/.agents/plugins"
fi
# summon.rs and local_recipes.rs use dirs::home_dir() for these, not Paths.
AGENTS_DIR="$HOME/.agents/agents"
RECIPES_DIR="$HOME/.agents/recipes"
# checks/mod.rs global_checks_dirs() hard-codes $HOME/.config/goose/checks.
CHECKS_DIR="$HOME/.config/goose/checks"
GLOBAL_HINTS="$CONFIG_DIR/.goosehints"
AGREEMENT_COPY="$CONFIG_DIR/$AGREEMENT_COPY_NAME"

if [ -n "${XDG_STATE_HOME:-}" ] && is_absolute "$XDG_STATE_HOME"; then
    STATE_DIR="$XDG_STATE_HOME/agent-harness"
else
    STATE_DIR="$HOME/.local/state/agent-harness"
fi
MANIFEST="$STATE_DIR/install-manifest.tsv"

PLUGIN_SRC="$HARNESS_ROOT/plugin/$PLUGIN_NAME"
PLUGIN_LINK="$PLUGINS_DIR/$PLUGIN_NAME"
MCP_TEMPLATE="$PLUGIN_SRC/.mcp.json.template"
MCP_RENDERED="$PLUGIN_SRC/.mcp.json"

say() { printf '%s\n' "$*"; }
warn() { WARNINGS=$((WARNINGS + 1)); printf 'warning: %s\n' "$*" >&2; }
act() {
    CHANGES=$((CHANGES + 1))
    if [ "$DRY_RUN" -eq 1 ]; then say "[dry-run] would $*"; else say "$*"; fi
}

checksum() { cksum <"$1" | awk '{print $1 "-" $2}'; }
exists() { [ -e "$1" ] || [ -L "$1" ]; }

# Manifest: one "kind<TAB>path<TAB>detail" row per artifact.
#   copy   path  checksum-of-what-we-wrote
#   link   path  link-target
#   block  path  created|existing   (marked block in the global .goosehints)
#   dir    path  -                  (directory we created; removed only if empty)
OLD_MANIFEST=""
if [ -f "$MANIFEST" ]; then OLD_MANIFEST="$(cat "$MANIFEST")"; fi
NEW_MANIFEST=""

manifest_detail() {
    local kind="$1" path="$2"
    printf '%s\n' "$OLD_MANIFEST" | awk -F '\t' -v k="$kind" -v p="$path" '$1 == k && $2 == p { print $3; exit }'
}
record() { NEW_MANIFEST="${NEW_MANIFEST}$1	$2	$3
"; }

ensure_dir() {
    local dir="$1" parent
    [ -d "$dir" ] && return 0
    parent="$(dirname "$dir")"
    [ -d "$parent" ] || ensure_dir "$parent"
    act "create directory $dir"
    if [ "$DRY_RUN" -eq 0 ]; then mkdir "$dir"; fi
    record dir "$dir" "-"
}

# Write $2 (a file) to $1 unless $1 belongs to someone else.
install_copy() {
    local dest="$1" src="$2" recorded current
    recorded="$(manifest_detail copy "$dest")"
    if ! exists "$dest"; then
        ensure_dir "$(dirname "$dest")"
        act "copy $src -> $dest"
        if [ "$DRY_RUN" -eq 0 ]; then
            cp "$src" "$dest.tmp.$$"
            mv "$dest.tmp.$$" "$dest"
        fi
        record copy "$dest" "$(checksum "$src")"
        return 0
    fi
    if [ -L "$dest" ] || [ ! -f "$dest" ]; then
        warn "skipping $dest: exists and is not a file this installer wrote"
        return 0
    fi
    current="$(checksum "$dest")"
    if [ -z "$recorded" ]; then
        if [ "$current" = "$(checksum "$src")" ]; then
            say "leaving $dest: identical file already present (not owned by installer)"
        else
            warn "skipping $dest: exists and was not created by this installer"
        fi
        return 0
    fi
    if [ "$current" != "$recorded" ]; then
        warn "skipping $dest: edited since install; not overwriting (remove it to reinstall)"
        record copy "$dest" "$recorded"
        return 0
    fi
    if cmp -s "$src" "$dest"; then
        record copy "$dest" "$recorded"
        return 0
    fi
    act "update $dest from $src"
    if [ "$DRY_RUN" -eq 0 ]; then
        cp "$src" "$dest.tmp.$$"
        mv "$dest.tmp.$$" "$dest"
    fi
    record copy "$dest" "$(checksum "$src")"
}

install_link() {
    local dest="$1" target="$2"
    if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$target" ]; then
        record link "$dest" "$target"
        return 0
    fi
    if exists "$dest"; then
        warn "skipping $dest: exists and is not this installer's link to $target"
        return 0
    fi
    ensure_dir "$(dirname "$dest")"
    act "link $dest -> $target"
    if [ "$DRY_RUN" -eq 0 ]; then ln -s "$target" "$dest"; fi
    record link "$dest" "$target"
}

# Literal token replacement; avoids ${var//pat/rep}, whose replacement quoting
# differs between bash 3.2 (macOS /bin/bash) and bash 5.2.
replace_token() {
    local rest="$1" out=""
    while case "$rest" in *"$TEMPLATE_TOKEN"*) true ;; *) false ;; esac do
        out="$out${rest%%"$TEMPLATE_TOKEN"*}$HARNESS_ROOT"
        rest="${rest#*"$TEMPLATE_TOKEN"}"
    done
    printf '%s\n' "$out$rest"
}

render_mcp_json() {
    local rendered
    case "$HARNESS_ROOT" in
        *'"'* | *\\*)
            warn "skipping .mcp.json: harness path contains a quote or backslash and cannot be written into JSON as-is"
            return 0
            ;;
    esac
    rendered="$(mktemp "${TMPDIR:-/tmp}/agent-harness-mcp.XXXXXX")"
    replace_token "$(cat "$MCP_TEMPLATE")" >"$rendered"
    install_copy "$MCP_RENDERED" "$rendered"
    rm -f "$rendered"
}

has_block() { [ -f "$1" ] && grep -Fxq "$BLOCK_BEGIN" "$1"; }

install_hints_block() {
    local hints="$GLOBAL_HINTS" origin
    if [ -L "$hints" ]; then
        warn "$hints is a symlink; goose skips a global hints file whose real path is outside $CONFIG_DIR (hints/import_files.rs sanitize_existing_path)"
    fi
    if has_block "$hints"; then
        origin="$(manifest_detail block "$hints")"
        record block "$hints" "${origin:-existing}"
        return 0
    fi
    if exists "$hints" && [ ! -f "$hints" ]; then
        warn "skipping $hints: exists and is not a regular file"
        return 0
    fi
    if exists "$hints"; then origin=existing; else origin=created; fi
    ensure_dir "$CONFIG_DIR"
    act "add @$AGREEMENT_COPY_NAME block to $hints"
    if [ "$DRY_RUN" -eq 0 ]; then
        if [ -s "$hints" ] && [ "$(tail -c 1 "$hints" | od -An -c | tr -d ' ')" != '\n' ]; then
            printf '\n' >>"$hints"
        fi
        printf '%s\n@%s\n%s\n' "$BLOCK_BEGIN" "$AGREEMENT_COPY_NAME" "$BLOCK_END" >>"$hints"
    fi
    record block "$hints" "$origin"
}

warn_if_context_names_exclude_goosehints() {
    local config_yaml="$CONFIG_DIR/config.yaml"
    if [ -f "$config_yaml" ] && grep -q '^CONTEXT_FILE_NAMES' "$config_yaml" &&
        ! grep -A5 '^CONTEXT_FILE_NAMES' "$config_yaml" | grep -q '\.goosehints'; then
        warn "$config_yaml sets CONTEXT_FILE_NAMES without .goosehints; the working agreement will not load until it is added"
    fi
}

remove_block() {
    local hints="$1" origin="$2" stripped
    has_block "$hints" || return 0
    act "remove agent-harness block from $hints"
    [ "$DRY_RUN" -eq 1 ] && return 0
    stripped="$(awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '$0 == b { skip = 1; next } skip && $0 == e { skip = 0; next } !skip' "$hints")"
    if [ "$origin" = "created" ] && [ -z "$(printf '%s' "$stripped" | tr -d '[:space:]')" ]; then
        rm -f "$hints"
    elif [ -n "$stripped" ]; then
        printf '%s\n' "$stripped" >"$hints"
    else
        : >"$hints"
    fi
}

# Remove one manifest artifact, if it is still exactly what we wrote.
remove_artifact() {
    local kind="$1" path="$2" detail="$3"
    case "$kind" in
        copy)
            if [ -f "$path" ] && [ ! -L "$path" ]; then
                if [ "$(checksum "$path")" = "$detail" ]; then
                    act "remove $path"
                    if [ "$DRY_RUN" -eq 0 ]; then rm -f "$path"; fi
                else
                    warn "keeping $path: edited since install"
                fi
            fi
            ;;
        link)
            if [ -L "$path" ] && [ "$(readlink "$path")" = "$detail" ]; then
                act "remove link $path"
                if [ "$DRY_RUN" -eq 0 ]; then rm -f "$path"; fi
            fi
            ;;
        block) remove_block "$path" "$detail" ;;
        dir) ;;
    esac
}

remove_empty_dirs() {
    printf '%s\n' "$1" | awk -F '\t' '$1 == "dir" { print length($2) "\t" $2 }' | sort -rn | cut -f2- |
        while IFS= read -r dir; do
            [ -n "$dir" ] || continue
            if [ -d "$dir" ] && [ -z "$(ls -A "$dir")" ]; then
                act "remove empty directory $dir"
                if [ "$DRY_RUN" -eq 0 ]; then rmdir "$dir"; fi
            fi
        done
}

# Directories created by an earlier run stay ours while they exist.
carry_over_dirs() {
    local kind path detail
    while IFS="	" read -r kind path detail; do
        if [ "$kind" = "dir" ] && [ -d "$path" ]; then record dir "$path" "$detail"; fi
    done <<EOF
$OLD_MANIFEST
EOF
}

write_manifest() {
    if [ -n "$NEW_MANIFEST" ]; then
        NEW_MANIFEST="$(printf '%s' "$NEW_MANIFEST" | sort -u)
"
    fi
    if [ "$NEW_MANIFEST" = "$OLD_MANIFEST
" ] || { [ -z "$NEW_MANIFEST" ] && [ -z "$OLD_MANIFEST" ]; }; then
        return 0
    fi
    [ "$DRY_RUN" -eq 1 ] && return 0
    if [ -z "$NEW_MANIFEST" ]; then
        rm -f "$MANIFEST"
        return 0
    fi
    printf '%s' "$NEW_MANIFEST" >"$MANIFEST.tmp.$$"
    mv "$MANIFEST.tmp.$$" "$MANIFEST"
}

is_agent_file() { [ "$(head -n 1 "$1")" = "---" ]; }

install_all() {
    local f rel desired kind path detail
    carry_over_dirs
    ensure_dir "$STATE_DIR"

    if [ -d "$HARNESS_ROOT/agents" ]; then
        for f in "$HARNESS_ROOT"/agents/*.md; do
            [ -f "$f" ] || continue
            is_agent_file "$f" || continue
            install_copy "$AGENTS_DIR/$(basename "$f")" "$f"
        done
    else
        say "skip agents: $HARNESS_ROOT/agents not present"
    fi

    if [ -d "$HARNESS_ROOT/recipes" ]; then
        while IFS= read -r rel; do
            [ -n "$rel" ] || continue
            install_copy "$RECIPES_DIR/$rel" "$HARNESS_ROOT/recipes/$rel"
        done <<EOF
$(cd "$HARNESS_ROOT/recipes" && find . \( -name tests -o -name '.?*' -o -name __pycache__ \) -prune -o -type f \( -name '*.yaml' -o -name '*.yml' -o -name '*.json' \) -print | sed 's|^\./||' | sort)
EOF
    else
        say "skip recipes: $HARNESS_ROOT/recipes not present"
    fi

    if [ -d "$HARNESS_ROOT/checks" ]; then
        for f in "$HARNESS_ROOT"/checks/*.md; do
            [ -f "$f" ] || continue
            [ "$(basename "$f")" = "README.md" ] && continue
            install_copy "$CHECKS_DIR/$(basename "$f")" "$f"
        done
    else
        say "skip checks: $HARNESS_ROOT/checks not present"
    fi

    if [ -d "$PLUGIN_SRC" ]; then
        if [ -f "$MCP_TEMPLATE" ]; then
            render_mcp_json
        else
            say "skip .mcp.json: $MCP_TEMPLATE not present"
        fi
        install_link "$PLUGIN_LINK" "$PLUGIN_SRC"
    else
        say "skip plugin: $PLUGIN_SRC not present"
    fi

    if [ -f "$HARNESS_ROOT/WORKING_AGREEMENT.md" ]; then
        install_copy "$AGREEMENT_COPY" "$HARNESS_ROOT/WORKING_AGREEMENT.md"
        install_hints_block
        warn_if_context_names_exclude_goosehints
    else
        say "skip working agreement: $HARNESS_ROOT/WORKING_AGREEMENT.md not present"
    fi

    # Prune artifacts from a previous install whose source has gone.
    desired="$(printf '%s' "$NEW_MANIFEST" | awk -F '\t' '{ print $1 "\t" $2 }')"
    while IFS="	" read -r kind path detail; do
        [ -n "$kind" ] || continue
        [ "$kind" = "dir" ] && continue
        if ! printf '%s\n' "$desired" | grep -Fxq "$kind	$path"; then
            remove_artifact "$kind" "$path" "$detail"
        fi
    done <<EOF
$OLD_MANIFEST
EOF
}

uninstall_all() {
    local kind path detail
    if [ -z "$OLD_MANIFEST" ]; then
        say "no manifest at $MANIFEST; looking only for the plugin link and hints block"
        if [ -L "$PLUGIN_LINK" ] && [ "$(readlink "$PLUGIN_LINK")" = "$PLUGIN_SRC" ]; then
            OLD_MANIFEST="link	$PLUGIN_LINK	$PLUGIN_SRC"
        fi
        if has_block "$GLOBAL_HINTS"; then
            OLD_MANIFEST="${OLD_MANIFEST:+$OLD_MANIFEST
}block	$GLOBAL_HINTS	existing"
        fi
    fi
    while IFS="	" read -r kind path detail; do
        [ -n "$kind" ] || continue
        remove_artifact "$kind" "$path" "$detail"
    done <<EOF
$OLD_MANIFEST
EOF
    if [ "$DRY_RUN" -eq 0 ]; then rm -f "$MANIFEST"; fi
    remove_empty_dirs "$OLD_MANIFEST"
    NEW_MANIFEST=""
}

say "harness root: $HARNESS_ROOT"
say "goose config dir: $CONFIG_DIR"
if [ "$UNINSTALL" -eq 1 ]; then uninstall_all; else install_all; fi
write_manifest

if [ "$CHANGES" -eq 0 ]; then say "nothing to do"; fi
if [ "$WARNINGS" -gt 0 ]; then say "$WARNINGS warning(s); see above"; fi
