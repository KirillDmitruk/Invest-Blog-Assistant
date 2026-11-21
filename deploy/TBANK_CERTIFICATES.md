# Scoped T-Bank trust bundle

`tbank-ca.pem` contains public CA certificates only, downloaded over verified HTTPS:

* Root: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
* Intermediate: https://gu-st.ru/content/lending/russian_trusted_sub_ca.cer

DER SHA-256 fingerprints:

* Russian Trusted Root CA: `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31`
* Russian Trusted Sub CA: `bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b`

Both certificates were parsed, their signatures checked (root self-signature and sub issued by root), and the official API TLS handshake tested. The intermediate expires 2027-03-06; update the bundle from the official source before then. The bundle is used only by `BrokerReadOnly.session.verify`. It is not installed into the operating system or used by Telegram, GitHub, or Cloudflare. Hostname and certificate validation remain enabled. Never put client certificates or private keys here.

T-Bank requirement: https://developer.tbank.ru/invest/intro/developer/network
