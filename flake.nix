{
  description = "Dev shell for the Futro S920 Yocto image and the OpenWrt AP fleet";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      inherit (pkgs) lib;

      # Nix's gcc/ld wrapper settings, handed to every bitbake task through a
      # BBPOSTCONF snippet so build/conf/local.conf stays host-neutral.
      # bitbake scrubs the task environment and only exports its own
      # preserved list, so a plain shell export would never reach the wrapper.
      #
      # NIX_DONT_SET_RPATH: the wrapper otherwise adds a RUNPATH to its own
      # glibc on every link. Native tools are linked against uninative's
      # ld.so (a different glibc), which would then load Nix's libc.so.6 and
      # die with "undefined symbol: __nptl_change_stack_perm" ("cannot run C
      # compiled programs" in configure). The opt-out is salt-suffixed; the
      # bare name is ignored.
      nixVars = {
        "NIX_DONT_SET_RPATH_${pkgs.stdenv.cc.suffixSalt}" = "1";
      };
      nixVarNames = lib.concatStringsSep " " (builtins.attrNames nixVars);
      # Ignored by the task hashes, so sstate signatures stay host-independent.
      bbPostConf = pkgs.writeText "home-router-nixvars.conf" ''
        ${lib.concatStringsSep "\n" (lib.mapAttrsToList (n: v: ''export ${n} = "${v}"'') nixVars)}
        BB_BASEHASH_IGNORE_VARS += "${nixVarNames}"
        BB_HASHCONFIG_IGNORE_VARS += "${nixVarNames}"
      '';

      # buildFHSEnv links /etc/{passwd,group,shadow} to the host's
      # (/.host-etc/...). A NixOS /etc/passwd lacks the standard accounts
      # OE-core's base-passwd expects, and shadow's open(O_NOFOLLOW) inside
      # `groupadd --root <sysroot>` (postinst-base-passwd, during
      # do_prepare_recipe_sysroot) fails with ELOOP on the symlink. Bind
      # writable copies instead: Debian-style system accounts plus the
      # invoking user, who must still resolve (ssh refuses to run for a uid
      # with no passwd entry).
      etcRwNames = [ "passwd" "group" "shadow" ];
      basePasswd = pkgs.writeText "home-router-passwd" ''
        root:x:0:0:root:/root:/bin/sh
        daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
        bin:x:2:2:bin:/bin:/usr/sbin/nologin
        sys:x:3:3:sys:/dev:/usr/sbin/nologin
        sync:x:4:65534:sync:/bin:/bin/sync
        games:x:5:60:games:/usr/games:/usr/sbin/nologin
        man:x:6:12:man:/var/cache/man:/usr/sbin/nologin
        lp:x:7:7:lp:/var/spool/lpd:/usr/sbin/nologin
        mail:x:8:8:mail:/var/mail:/usr/sbin/nologin
        news:x:9:9:news:/var/spool/news:/usr/sbin/nologin
        uucp:x:10:10:uucp:/var/spool/uucp:/usr/sbin/nologin
        proxy:x:13:13:proxy:/bin:/usr/sbin/nologin
        www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin
        backup:x:34:34:backup:/var/backups:/usr/sbin/nologin
        list:x:38:38:Mailing List Manager:/var/list:/usr/sbin/nologin
        irc:x:39:39:ircd:/run/ircd:/usr/sbin/nologin
        gnats:x:41:41:Gnats:/var/lib/gnats:/usr/sbin/nologin
        nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin
      '';
      baseGroup = pkgs.writeText "home-router-group" ''
        root:x:0:
        daemon:x:1:
        bin:x:2:
        sys:x:3:
        adm:x:4:
        tty:x:5:
        disk:x:6:
        lp:x:7:
        mail:x:8:
        news:x:9:
        uucp:x:10:
        man:x:12:
        proxy:x:13:
        kmem:x:15:
        dialout:x:20:
        fax:x:21:
        voice:x:22:
        cdrom:x:24:
        floppy:x:25:
        tape:x:26:
        sudo:x:27:
        audio:x:29:
        dip:x:30:
        www-data:x:33:
        backup:x:34:
        operator:x:37:
        list:x:38:
        irc:x:39:
        src:x:40:
        gnats:x:41:
        shadow:x:42:
        utmp:x:43:
        video:x:44:
        sasl:x:45:
        plugdev:x:46:
        staff:x:50:
        games:x:60:
        users:x:100:
        nogroup:x:65534:
      '';

      # Yocto (bitbake host-tool checks, uninative, prebuilt sstate) and the
      # OpenWrt Image Builder both assume a conventional /usr/bin + /lib
      # layout, so the shell is an FHS sandbox (bubblewrap) rather than a
      # plain mkShell.
      fhs = pkgs.buildFHSEnv {
        name = "home-router-env";

        targetPkgs = p: with p; [
          # Yocto host requirements (docs.yoctoproject.org "Required Packages")
          gcc
          gnumake
          binutils
          gawk
          wget
          git
          diffstat
          unzip
          texinfo
          chrpath
          socat
          cpio
          file
          which
          hostname
          iputils
          xz
          zstd
          lz4
          bzip2
          gzip
          gnutar
          patch
          perl
          rsync
          util-linux
          acl
          ncurses
          openssl
          rpcsvc-proto # rpcgen
          (python3.withPackages (ps: with ps; [ pexpect gitpython jinja2 subunit websockets ]))

          # Real headers under /usr/include. The gcc wrapper finds them in the
          # store on its own, but perl-native's Errno_pm.PL looks for
          # /usr/include/errno.h and otherwise falls back to `gcc -E -P`,
          # whose output has no line markers -> "No error definitions found".
          glibc.dev
          linuxHeaders

          # aps/ (Image Builder, gen-ap-tls.py) and secret generation
          mkpasswd
          openssh

          # scripts/run-qemu.sh
          qemu
        ];

        # Runs on the host before bwrap, for both `nix develop` and `nix run`.
        extraPreBwrapCmds = ''
          HOME_ROUTER_ETC_RW="''${XDG_RUNTIME_DIR:-/tmp}/home-router-env-etc.$UID"
          ${pkgs.coreutils}/bin/install -d -m 700 "$HOME_ROUTER_ETC_RW"
          # The /tmp fallback is predictable; refuse a dir someone else made.
          if [ -L "$HOME_ROUTER_ETC_RW" ] || [ ! -O "$HOME_ROUTER_ETC_RW" ]; then
            echo "home-router-env: $HOME_ROUTER_ETC_RW is not a directory owned by you" >&2
            exit 1
          fi
          { ${pkgs.coreutils}/bin/cat ${basePasswd}; ${pkgs.getent}/bin/getent passwd "$UID"; } > "$HOME_ROUTER_ETC_RW/passwd"
          { ${pkgs.coreutils}/bin/cat ${baseGroup}; ${pkgs.getent}/bin/getent group "$(${pkgs.coreutils}/bin/id -g)"; } > "$HOME_ROUTER_ETC_RW/group"
          : > "$HOME_ROUTER_ETC_RW/shadow"
          ${pkgs.coreutils}/bin/chmod 644 "$HOME_ROUTER_ETC_RW/passwd" "$HOME_ROUTER_ETC_RW/group"
        '';
        extraBwrapArgs = lib.concatMap
          (name: [ "--bind" "\${HOME_ROUTER_ETC_RW}/${name}" "/etc/${name}" ])
          etcRwNames
        # NixOS runs nscd, and glibc asks its socket before reading the files
        # above, so getpwnam/getgrnam would answer from the host's database
        # (no `mail`, `audio` at the wrong gid). Hide it.
        ++ [ "--tmpfs" "/run/nscd" ];
        # A placeholder at /etc/X in the rootfs makes buildFHSEnv bind it
        # instead of symlinking to /.host-etc/X; the --bind above overlays it.
        extraBuildCommands = ''
          mkdir -p $out/etc
          ${lib.concatMapStringsSep "\n" (name: ": > $out/etc/${name}") etcRwNames}

          # Nix's gcc wrapper omits the LTO shims, and bitbake lists gcc-ar in
          # HOSTTOOLS_NONFATAL, so a missing one is skipped silently until a
          # recipe needs it (u-boot-tools-native: "gcc-ar: command not
          # found"). Take them from the unwrapped gcc.
          for t in gcc-ar gcc-nm gcc-ranlib; do
            ln -s ${pkgs.gcc.cc}/bin/$t $out/usr/bin/$t
          done
        '';

        profile = ''
          # bitbake refuses to start without a UTF-8 locale.
          export LOCALE_ARCHIVE=${pkgs.glibcLocales}/lib/locale/locale-archive
          export LANG=en_US.UTF-8
          export LC_ALL=en_US.UTF-8
          # run-qemu.sh honours a pre-set OVMF_FW when /usr/share/OVMF is absent.
          export OVMF_FW=${pkgs.OVMF.fd}/FV/OVMF.fd
          export BBPOSTCONF=${bbPostConf}
        '';

        runScript = "bash";
      };
    in
    {
      packages.${system}.default = fhs;
      devShells.${system}.default = fhs.env;
    };
}
