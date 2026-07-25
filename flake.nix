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
            # uv sync + 仮想環境有効化
            uv sync --frozen || uv sync
            source .venv/bin/activate
          '';
        };
      }
    );
}
