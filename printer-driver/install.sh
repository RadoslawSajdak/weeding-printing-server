#!/usr/bin/env bash
# Weeding Gallery Printer Daemon — setup / teardown
# Run as root from the printer-driver/ directory:
#   bash install.sh           # install (IPP-over-USB via ipp-usb 0.9.20)
#   bash install.sh --usb     # install (USB via CUPS + Gutenprint)
#   bash install.sh --update  # update daemon files only
#   bash install.sh --remove  # uninstall

set -euo pipefail

INSTALL_DIR=/opt/weeding-printer
SERVICE_NAME=weeding-printer
ENV_FILE=/etc/weeding-printer.env
VENV=$INSTALL_DIR/venv
UNIT=/etc/systemd/system/${SERVICE_NAME}.service
PRINTER_CUPS_NAME="Selphy-CP1500"

IPP_USB_VERSION="0.9.20"

GUTENPRINT_VERSION="5.3.5"
GUTENPRINT_TAR="gutenprint-${GUTENPRINT_VERSION}.tar.xz"
GUTENPRINT_URL="${GUTENPRINT_URL:-}"

MODE=install
USB_MODE=0
for arg in "$@"; do
    case "$arg" in
        --remove) MODE=remove ;;
        --update) MODE=update ;;
        --usb)    USB_MODE=1  ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

build_ipp_usb() {
    echo "==> Building ipp-usb $IPP_USB_VERSION from source"
    apt-get install -y --no-install-recommends \
        golang-go git libusb-1.0-0-dev libavahi-client-dev pkg-config

    local build_dir
    build_dir=$(mktemp -d)
    git clone --depth 1 --branch "$IPP_USB_VERSION" \
        https://github.com/OpenPrinting/ipp-usb "$build_dir"
    go build -C "$build_dir" -ldflags "-s -w" -tags nethttpomithttp2
    install -m 755 "$build_dir/ipp-usb" /usr/sbin/ipp-usb
    rm -rf "$build_dir"

    # Prevent apt from overwriting with 0.9.23
    apt-mark hold ipp-usb 2>/dev/null || true
    echo "    ipp-usb $IPP_USB_VERSION installed and held"
}

build_gutenprint() {
    echo "==> Building gutenprint $GUTENPRINT_VERSION from source"
    apt-get install -y --no-install-recommends \
        build-essential automake libtool libcups2-dev libcupsimage2-dev

    if [[ ! -f "$GUTENPRINT_TAR" ]]; then
        [[ -z "$GUTENPRINT_URL" ]] && {
            echo "ERROR: $GUTENPRINT_TAR not found and GUTENPRINT_URL is not set."
            exit 1
        }
        curl -fL "$GUTENPRINT_URL" -o "$GUTENPRINT_TAR"
    fi

    local build_dir
    build_dir=$(mktemp -d)
    tar -xf "$GUTENPRINT_TAR" -C "$build_dir" --strip-components=1
    pushd "$build_dir" > /dev/null
    ./configure --with-cups --disable-test --disable-samples \
                --disable-nls --without-gimp2 2>&1 | tail -3
    make -j"$(nproc)" 2>&1 | tail -3
    make install 2>&1 | tail -3
    popd > /dev/null
    rm -rf "$build_dir"
    echo "    gutenprint $GUTENPRINT_VERSION installed"
}

# ── Update ────────────────────────────────────────────────────────────────────

do_update() {
    [[ -d "$INSTALL_DIR" ]] || { echo "ERROR: $INSTALL_DIR not found — run install first."; exit 1; }

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    cp daemon.py printer.py requirements.txt "$INSTALL_DIR/"
    chown -R printer:lp "$INSTALL_DIR"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    chown -R printer:lp "$VENV"
    cp weeding-printer.service "$UNIT"
    systemctl daemon-reload
    systemctl start "$SERVICE_NAME"
    echo "Update complete. Logs: journalctl -u $SERVICE_NAME -f"
}

# ── Remove ────────────────────────────────────────────────────────────────────

do_remove() {
    systemctl stop    "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    rm -rf "$INSTALL_DIR"
    lpadmin -x "$PRINTER_CUPS_NAME" 2>/dev/null || true
    id printer &>/dev/null && userdel printer || true
    echo ""
    echo "Removed. ENV file kept at $ENV_FILE — delete manually if needed."
    echo "ipp-usb binary and hold not touched — remove manually if needed:"
    echo "  apt-mark unhold ipp-usb && apt-get install --reinstall ipp-usb"
    echo ""
}

# ── Install ───────────────────────────────────────────────────────────────────

install_common() {
    apt-get install -y --no-install-recommends \
        cups cups-client \
        curl python3 python3-venv python3-dev libcups2-dev gcc ca-certificates
    systemctl enable --now cups
}

register_printer_ipp() {
    local port="$1"
    local uri="ipp://localhost:${port}/ipp/print"
    lpadmin -x "$PRINTER_CUPS_NAME" 2>/dev/null || true
    lpadmin -p "$PRINTER_CUPS_NAME" -E -v "$uri" -m everywhere -D "Canon Selphy CP1500"
    lpoptions -d "$PRINTER_CUPS_NAME"
    echo "==> Printer registered: $uri"
}

register_printer_usb() {
    local uri="$1"
    local ppd
    ppd=$(lpinfo -m 2>/dev/null \
        | grep -i "cp.1500\|cp1500" | grep -i gutenprint | head -1 | awk '{print $1}')
    lpadmin -x "$PRINTER_CUPS_NAME" 2>/dev/null || true
    if [[ -n "$ppd" ]]; then
        lpadmin -p "$PRINTER_CUPS_NAME" -E -v "$uri" -m "$ppd" -D "Canon Selphy CP1500"
    else
        echo "WARN: Gutenprint PPD not found — falling back to IPP Everywhere"
        lpadmin -p "$PRINTER_CUPS_NAME" -E -v "$uri" -m everywhere -D "Canon Selphy CP1500"
    fi
    lpoptions -d "$PRINTER_CUPS_NAME"
    echo "==> Printer registered: $uri"
}

do_install() {
    apt-get update -qq
    install_common

    if [[ "$USB_MODE" -eq 1 ]]; then
        # ── USB mode: CUPS + Gutenprint + usblp ──────────────────────────────
        echo "==> USB mode"
        apt-get install -y --no-install-recommends printer-driver-gutenprint

        if ! lpinfo -m 2>/dev/null | grep -qi "cp.1500\|cp1500"; then
            build_gutenprint
        fi

        echo ""
        echo "Plug in the Canon Selphy CP1500 via USB and wait..."
        until lpinfo -v 2>/dev/null | grep -iE "selphy|cp1500" > /dev/null; do
            printf "."; sleep 2
        done
        echo " detected!"
        PRINTER_URI=$(lpinfo -v | grep -iE "selphy|cp1500" | head -1 | awk '{print $2}')
        register_printer_usb "$PRINTER_URI"

    else
        # ── IPP mode: ipp-usb 0.9.20 built from source ───────────────────────
        echo "==> IPP mode (ipp-usb $IPP_USB_VERSION)"
        apt-get install -y --no-install-recommends ipp-usb
        build_ipp_usb

        echo ""
        echo "Plug in the Canon Selphy CP1500 via USB and wait..."
        IPP_PORT=""
        until [[ -n "$IPP_PORT" ]]; do
            IPP_PORT=$(ss -tlnp 2>/dev/null \
                | grep -oP '(?<=:)(6[0-9]{4})\b' | head -1 || true)
            [[ -z "$IPP_PORT" ]] && { printf "."; sleep 2; }
        done
        echo " port $IPP_PORT open"

        echo "==> Testing IPP endpoint"
        curl -s "http://localhost:${IPP_PORT}/ipp/print" 2>&1 | head -5

        register_printer_ipp "$IPP_PORT"
    fi

    # ── System user ───────────────────────────────────────────────────────────
    if ! id printer &>/dev/null; then
        useradd --system --no-create-home --groups lp,lpadmin printer
    else
        usermod -aG lp,lpadmin printer
    fi

    # ── Application files ─────────────────────────────────────────────────────
    mkdir -p "$INSTALL_DIR"
    cp daemon.py printer.py requirements.txt "$INSTALL_DIR/"
    chown -R printer:lp "$INSTALL_DIR"

    # ── Python venv ───────────────────────────────────────────────────────────
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
    chown -R printer:lp "$VENV"

    # ── Env file ──────────────────────────────────────────────────────────────
    if [[ ! -f "$ENV_FILE" ]]; then
        cp weeding-printer.env.example "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        chown root:root "$ENV_FILE"
        echo ""
        echo "*** Edit $ENV_FILE and set API_BASE + PRINTER_API_KEY ***"
        echo ""
    fi

    # ── systemd ───────────────────────────────────────────────────────────────
    cp weeding-printer.service "$UNIT"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    echo ""
    echo "========================================"
    echo " Setup complete"
    echo "========================================"
    echo ""
    echo "  Edit config:  nano $ENV_FILE"
    echo "  Start daemon: systemctl start $SERVICE_NAME"
    echo "  Watch logs:   journalctl -u $SERVICE_NAME -f"
    echo "  Uninstall:    bash install.sh --remove"
    echo ""
}

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "$MODE" in
    remove) do_remove ;;
    update) do_update ;;
    *)      do_install ;;
esac
