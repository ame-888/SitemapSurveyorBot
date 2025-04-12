{pkgs}: {
  deps = [
    pkgs.zlib
    pkgs.xcodebuild
    pkgs.libopus
    pkgs.ffmpeg-full
    pkgs.postgresql
    pkgs.openssl
  ];
}
