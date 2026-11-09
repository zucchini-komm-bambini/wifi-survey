# Lokales Paket – bauen und installieren mit:  makepkg -si
pkgname=wifi-survey
pkgver=0.4.0
pkgrel=1
pkgdesc="WLAN an Messpunkten vermessen und Netzwerk-Setups vergleichen (TUI + CLI)"
arch=('any')
license=('MIT')
depends=('python' 'python-textual' 'iw' 'iputils' 'iproute2' 'networkmanager')
optdepends=('iperf3: Durchsatztest gegen einen iperf3-Server'
            'gum: hübschere Auswahlmenüs im CLI-Modus')

package() {
  cd "$startdir"
  local site
  site=$(python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')

  install -Dm644 -t "$pkgdir$site/wifisurvey" wifisurvey/*.py
  python -m compileall -q -d "$site/wifisurvey" "$pkgdir$site/wifisurvey"

  install -dm755 "$pkgdir/usr/bin"
  printf '#!/usr/bin/python3\nfrom wifisurvey.cli import main\nmain()\n' > "$pkgdir/usr/bin/wifi-survey"
  chmod 755 "$pkgdir/usr/bin/wifi-survey"

  install -Dm644 wifi-survey.desktop "$pkgdir/usr/share/applications/wifi-survey.desktop"
}
