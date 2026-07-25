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
            # .env 読み込み（既存の仕組みを維持）
            if [ -f .env ]; then
              while IFS='=' read -r key val; do
                [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
                export "$key=$val"
              done < .env
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
