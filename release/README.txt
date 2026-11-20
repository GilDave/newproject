Solomon 1.11.1 release files
---------------------------
solomon-1.11.1.tar.gz            full source bundle (install.sh, scripts, USER_GUIDE.md, benchmarks). Recommended:
                                  tar xzf solomon-1.11.1.tar.gz && cd solomon-1.11.1 && bash install.sh
solomon-1.11.1-py3-none-any.whl  Python wheel (needs Python 3.11+, no other dependencies):
                                  python3 -m venv .venv && .venv/bin/pip install solomon-1.11.1-py3-none-any.whl
                                  .venv/bin/solomon selftest && .venv/bin/solomon dashboard
                                (start.sh / stop.sh background scripts are only in the source bundle or git checkout)
SHA256SUMS                      checksums

Rebuild:  python3 -m pip install build && python3 -m build -o release .   (then refresh SHA256SUMS)
