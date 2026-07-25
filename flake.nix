{
  description = "HW-Genie — Hero Wars automation toolkit";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            python313
            uv
            turso-cli
            ruff
            pytest
            sqlite
            sqlite-interactive
          ];

          shellHook = ''
            # .env 読み込み（シェルの安全な source 機構を利用）
            if [ -f .env ]; then
              set -a
              . .env
              set +a
            fi
            # uv sync + 仮想環境有効化
            uv sync --frozen 2>/dev/null || uv sync
            source .venv/bin/activate
            export PYTHONWARNINGS="ignore::DeprecationWarning"
          '';
        };
      }
    );
}
