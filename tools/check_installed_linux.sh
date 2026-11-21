#!/usr/bin/env bash
# Run only on the disposable GitHub Actions Linux packaging runner.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_OS:-} == Linux ]]
[[ $# == 1 && -f "$1" && $(dpkg-deb -f "$1" Package) == nn-remote ]]
if dpkg-query -W -f='${db:Status-Status}' nn-remote 2>/dev/null | grep -qx installed; then
  echo "Refusing to replace an existing nn-remote installation" >&2
  exit 1
fi
sudo apt-get install -y "$(realpath "$1")"
trap 'sudo dpkg --purge nn-remote' EXIT
entry=/usr/share/applications/nn-remote.desktop
desktop-file-validate "$entry"
test -f /usr/share/icons/hicolor/128x128/apps/nn-remote.png
/opt/nn-remote/NNRemote --check-ui-assets
xvfb-run -a -s '-screen 0 1280x900x24' bash -euo pipefail -c '
  openbox >/dev/null 2>&1 &
  wm=$!
  trap "kill $wm 2>/dev/null || true" EXIT
  for i in {1..50}; do
    if wmctrl -m >/dev/null 2>&1; then break; fi
    sleep .2
  done
  gio launch "$1"
  window=$(timeout 25 xdotool search --onlyvisible --sync --name "^nn远程 · 连接设备$" | head -n 1)
  test -n "$window"
  wmctrl -ic "$(printf "0x%x" "$window")"
  for i in {1..50}; do
    if ! xdotool getwindowname "$window" >/dev/null 2>&1; then exit 0; fi
    sleep .2
  done
  exit 1
' _ "$entry"
sudo dpkg --purge nn-remote
trap - EXIT
test ! -e "$entry"
test ! -e /opt/nn-remote/NNRemote
