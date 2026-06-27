#!/usr/bin/env bash
# Complete setup / teardown of the Weeding Gallery Printer Daemon on Raspberry Pi OS Lite.
# Run as root from the printer-driver/ directory:
#   sudo bash install.sh            # install
#   sudo bash install.sh --remove   # uninstall

set -euo pipefail

INSTALL_DIR=/opt/weeding-printer
SERVICE_NAME=weeding-printer
ENV_FILE=/etc/weeding-printer.env
VENV=$INSTALL_DIR/venv
UNIT=/etc/systemd/system/${SERVICE_NAME}.service

PRINTER_CUPS_NAME="Selphy-CP1500"
PRINTER_URI=""  # discovered dynamically via lpinfo -v

GUTENPRINT_VERSION="5.3.5"
GUTENPRINT_TAR="gutenprint-${GUTENPRINT_VERSION}.tar.xz"
# Tarball must be placed next to install.sh, or set GUTENPRINT_URL to download it.
GUTENPRINT_URL="${GUTENPRINT_URL:-}"

# ── Argument parsing ──────────────────────────────────────────────────────────

MODE=install
case "${1:-}" in
    --remove) MODE=remove ;;
    --update) MODE=update ;;
esac

# ── Helpers ───────────────────────────────────────────────────────────────────

build_gutenprint() {
    echo "==> Building gutenprint $GUTENPRINT_VERSION from source"

    apt-get install -y --no-install-recommends \
        build-essential automake libtool \
        libcups2-dev libcupsimage2-dev

    local src="$GUTENPRINT_TAR"

    if [[ ! -f "$src" ]]; then
        if [[ -z "$GUTENPRINT_URL" ]]; then
            echo ""
            echo "ERROR: $src not found and GUTENPRINT_URL is not set."
            echo "  Download gutenprint $GUTENPRINT_VERSION and place it here, or set:"
            echo "    GUTENPRINT_URL=https://… bash install.sh"
            exit 1
        fi
        echo "    downloading from $GUTENPRINT_URL"
        curl -fL "$GUTENPRINT_URL" -o "$src"
    fi

    local build_dir
    build_dir=$(mktemp -d)
    tar -xf "$src" -C "$build_dir" --strip-components=1

    pushd "$build_dir" > /dev/null
    ./configure \
        --with-cups \
        --disable-test \
        --disable-samples \
        --disable-nls \
        --without-gimp2 \
        2>&1 | tail -5
    make -j"$(nproc)" 2>&1 | tail -5
    make install 2>&1 | tail -5
    popd > /dev/null

    rm -rf "$build_dir"

    # Refresh CUPS PPD database
    /usr/lib/cups/driver/gutenprint.5.3 org.openprinting.categories 2>/dev/null || true
    echo "    gutenprint $GUTENPRINT_VERSION installed"
}

# ── Update ───────────────────────────────────────────────────────────────────

do_update() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        echo "ERROR: $INSTALL_DIR not found — run install first."
        exit 1
    fi

    echo "==> Stopping $SERVICE_NAME"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    echo "==> Copying application files to $INSTALL_DIR"
    cp daemon.py printer.py requirements.txt "$INSTALL_DIR/"
    chown -R printer:lp "$INSTALL_DIR"

    echo "==> Updating Python dependencies"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    chown -R printer:lp "$VENV"

    echo "==> Installing updated systemd unit"
    cp weeding-printer.service "$UNIT"
    systemctl daemon-reload

    echo "==> Starting $SERVICE_NAME"
    systemctl start "$SERVICE_NAME"

    echo ""
    echo "========================================"
    echo " Update complete"
    echo "========================================"
    echo ""
    echo " Watch logs: journalctl -u $SERVICE_NAME -f"
    echo ""
}

# ── Remove ────────────────────────────────────────────────────────────────────

do_remove() {
    echo "==> Stopping and disabling $SERVICE_NAME"
    systemctl stop    "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true

    echo "==> Removing systemd unit"
    rm -f "$UNIT"
    systemctl daemon-reload

    echo "==> Removing application files ($INSTALL_DIR)"
    rm -rf "$INSTALL_DIR"

    echo "==> Keeping env file ($ENV_FILE) — remove manually if needed"

    echo "==> Removing 'printer' system user"
    if id printer &>/dev/null; then
        userdel printer
    fi

    echo "==> Removing $PRINTER_CUPS_NAME from CUPS"
    lpadmin -x "$PRINTER_CUPS_NAME" 2>/dev/null || true

    echo "==> Purging installed packages"
    apt-get purge -y \
        printer-driver-gutenprint \
        python3-dev \
        libcups2-dev \
        gcc
    apt-get autoremove -y

    echo ""
    echo "========================================"
    echo " Uninstall complete"
    echo "========================================"
    echo ""
    echo " Note: cups / cups-client / cups-filters were kept."
    echo "       Remove manually if needed:"
    echo "         apt-get purge cups cups-client cups-filters"
    echo ""
    echo " Note: gutenprint built from source (if any) must be"
    echo "       removed manually from /usr/local/."
    echo ""
}

# ── Install ───────────────────────────────────────────────────────────────────

do_install() {
    # ── 1. System packages ────────────────────────────────────────────────────

    echo "==> Updating package lists"
    apt-get update -qq

    echo "==> Installing CUPS and build dependencies"
    apt-get install -y --no-install-recommends \
        cups \
        cups-client \
        cups-filters \
        printer-driver-gutenprint \
        curl \
        python3 \
        python3-venv \
        python3-dev \
        libcups2-dev \
        gcc \
        ca-certificates

    # ── 2. Enable CUPS ────────────────────────────────────────────────────────

    echo "==> Enabling CUPS"
    systemctl enable --now cups

    # ── 3. Gutenprint — upgrade to 5.3.5 if CP1500 not in repo version ───────

    if lpinfo -m 2>/dev/null | grep -qi "cp.1500\|cp1500"; then
        echo "==> gutenprint already has CP1500 support — skipping source build"
    else
        echo "==> gutenprint $(dpkg-query -W -f='${Version}' printer-driver-gutenprint 2>/dev/null) has no CP1500 — building $GUTENPRINT_VERSION from source"
        build_gutenprint
    fi

    # ── 4. Wait for printer and register in CUPS ──────────────────────────────

    echo ""
    echo "════════════════════════════════════════════════"
    echo " Plug in the Canon Selphy CP1500 via USB now."
    echo " Waiting for CUPS to see it…"
    echo "════════════════════════════════════════════════"

    # lpinfo -v is the authoritative source.
    # CP1500 may appear as:
    #   usb://Canon/SELPHY%20CP1500   (USB backend — most likely)
    #   ipp://localhost:60000/...     (ipp-usb, only if printer supports IPP-over-USB)
    until lpinfo -v 2>/dev/null | grep -iE "selphy|cp1500|cp-1500" > /dev/null; do
        printf "."
        sleep 2
    done
    echo " detected!"
    echo ""

    DISCOVERED_URI=$(lpinfo -v 2>/dev/null \
        | grep -iE "selphy|cp1500|cp-1500" \
        | head -1 \
        | awk '{print $2}')
    PRINTER_URI="${DISCOVERED_URI}"
    echo "==> Device URI: $PRINTER_URI"

    echo "==> Registering $PRINTER_CUPS_NAME in CUPS"
    lpadmin -x "$PRINTER_CUPS_NAME" 2>/dev/null || true

    GUTENPRINT_PPD=$(lpinfo -m 2>/dev/null \
        | grep -i "cp.1500\|cp1500\|selphy.*1500\|1500.*selphy" \
        | grep -i gutenprint \
        | head -1 \
        | awk '{print $1}')

    if [[ -n "$GUTENPRINT_PPD" ]]; then
        echo "    PPD: $GUTENPRINT_PPD"
        lpadmin -p "$PRINTER_CUPS_NAME" -E -v "$PRINTER_URI" \
                -m "$GUTENPRINT_PPD" -D "Canon Selphy CP1500"
    else
        echo "    gutenprint PPD not found — falling back to IPP Everywhere"
        lpadmin -p "$PRINTER_CUPS_NAME" -E -v "$PRINTER_URI" \
                -m everywhere -D "Canon Selphy CP1500"
    fi

    lpoptions -d "$PRINTER_CUPS_NAME"
    echo "==> Printer registered and set as default"

    # ── 5. System user ────────────────────────────────────────────────────────

    echo "==> Ensuring 'printer' user exists"
    if ! id printer &>/dev/null; then
        useradd --system --no-create-home --groups lp,lpadmin printer
    else
        usermod -aG lp,lpadmin printer
    fi

    # ── 6. Application files ──────────────────────────────────────────────────

    echo "==> Copying files to $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    cp daemon.py printer.py requirements.txt "$INSTALL_DIR/"
    chown -R printer:lp "$INSTALL_DIR"

    # ── 7. Python virtual environment ─────────────────────────────────────────

    echo "==> Creating Python venv"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    chown -R printer:lp "$VENV"

    # ── 8. Environment / config file ──────────────────────────────────────────

    echo "==> Installing env file"
    if [[ ! -f "$ENV_FILE" ]]; then
        cp weeding-printer.env.example "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        chown root:root "$ENV_FILE"
        echo ""
        echo "  *** Edit $ENV_FILE and set API_BASE + PRINTER_API_KEY ***"
        echo ""
    else
        echo "  (keeping existing $ENV_FILE)"
    fi

    # ── 9. systemd unit ───────────────────────────────────────────────────────

    echo "==> Installing systemd unit"
    cp weeding-printer.service "$UNIT"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    # ── 10. Summary ───────────────────────────────────────────────────────────

    echo ""
    echo "========================================"
    echo " Setup complete"
    echo "========================================"
    echo ""
    echo " Next steps:"
    echo "  1. Edit the config:   nano $ENV_FILE"
    echo "  2. Start the daemon:  systemctl start $SERVICE_NAME"
    echo "  3. Watch logs:        journalctl -u $SERVICE_NAME -f"
    echo ""
    echo " To uninstall:          bash install.sh --remove"
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "$MODE" in
    remove) do_remove ;;
    update) do_update ;;
    *)      do_install ;;
esac
