#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

main() {
  echo "ChatVector local setup"
  echo "======================"

  check_prerequisites
  check_docker_daemon

  configure_backend_provider

  ensure_frontend_env
  install_frontend_dependencies
  prepare_docker_services

  if is_provider_configuration_complete; then
    cat <<EOF

Setup complete.

Next steps:
  make quickstart  Full setup + start (safe to rerun)
  make             Start backend + frontend and open browser tabs
  make dev         Start without opening browser tabs

URLs:
  Frontend demo : http://localhost:3000
  API docs      : http://localhost:8000/docs

Existing env files are never overwritten. To change providers later, edit backend/.env.
EOF
  else
    cat <<EOF

Setup finished. Provider configuration is not complete yet.

Edit backend/.env, then run:
  make

Or rerun:
  make setup
EOF
  fi
}

main "$@"
