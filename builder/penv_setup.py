# Copyright 2014-present PlatformIO <contact@platformio.org>
# Licensed under the Apache License, Version 2.0

import json
import os
import re
import shutil
import semantic_version
import site
import socket
import subprocess
import sys
from pathlib import Path

from platformio.package.version import pepver_to_semver
from platformio.compat import IS_WINDOWS

# Enforce Python 3.10 minimum requirement
if sys.version_info < (3, 10):
    sys.stderr.write(
        f"Error: Python 3.10 or higher is required. "
        f"Current version: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}\n"
        "Please update your Python installation.\n"
    )
    sys.exit(1)

github_actions = bool(os.getenv("GITHUB_ACTIONS"))

PLATFORMIO_URL_VERSION_RE = re.compile(
    r'/v?(\d+\.\d+\.\d+(?:[.-]\w+)?(?:\.\d+)?)(?:\.(?:zip|tar\.gz|tar\.bz2))?$',
    re.IGNORECASE,
)

# Required dependencies for ESP32 platform builds
python_deps = {
    "platformio": "https://github.com/pioarduino/platformio-core/archive/refs/tags/v6.1.18.zip",
    "pyyaml": ">=6.0.2",
    "rich-click": ">=1.8.6",
    "zopfli": ">=0.2.2",
    "intelhex": ">=2.3.0",
    "rich": ">=14.0.0",
    "urllib3": "<2",
    "cryptography": ">=45.0.3",
    "certifi": ">=2025.8.3",
    "ecdsa": ">=0.19.1",
    "bitstring": ">=4.3.1",
    "reedsolo": ">=1.5.3,<1.8",
    "esp-idf-size": ">=1.6.1"
}

def has_internet_connection(host="1.1.1.1", port=53, timeout=2):
    """
    Checks if internet connection is available.
    Returns True if a socket connection can be made, False otherwise.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def get_executable_path(penv_dir, executable_name):
    """
    Build the path to an executable inside the virtual environment.

    Args:
        penv_dir (str): Path to the virtual environment root.
        executable_name (str): Executable name ("python", "uv", etc.)

    Returns:
        str: Absolute path to the requested executable
    """
    exe_suffix = ".exe" if IS_WINDOWS else ""
    scripts_dir = "Scripts" if IS_WINDOWS else "bin"
    return str(Path(penv_dir) / scripts_dir / f"{executable_name}{exe_suffix}")

def ensure_penv_with_uv(platformio_dir):
    """
    Ensure that penv is created using uv and contains a marker file.
    If not, deletes the old penv and recreates it with uv, writing "pioarduino_py" marker.

    Args:
        platformio_dir (str): Path to PlatformIO root

    Returns:
        str: Path to penv directory
    """
    penv_dir = Path(platformio_dir) / "penv"
    marker_file = penv_dir / "pioarduino_py"
    penv_python = get_executable_path(penv_dir, "python")
    if not marker_file.is_file() or not os.path.isfile(penv_python):
        print("Marker file missing or penv corrupted, removing and recreating using uv.")
        if penv_dir.exists():
            shutil.rmtree(penv_dir)
        uv_cmd = "uv"
        python_exe = sys.executable
        try:
            subprocess.check_call([
                uv_cmd, "venv", "--clear", f"--python={python_exe}", str(penv_dir)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        except Exception as exc:
            sys.stderr.write(f"Error: Failed to create penv with uv: {exc}\n")
            sys.exit(1)
        # Write marker after successful (re)creation
        marker_file.write_text("required by pioarduino\n")
    return str(penv_dir)

def setup_pipenv_in_package(env, penv_dir):
    """
    Ensure penv exists within package folder, creating it via uv if missing.
    Fallback to python -m venv if uv fails.

    Args:
        env: SCons environment object
        penv_dir (str): Environment directory to create

    Returns:
        str or None: 'uv' if created with uv, else None
    """
    if not os.path.isfile(get_executable_path(penv_dir, "python")):
        uv_success = False
        uv_cmd = None
        try:
            python_exe = env.subst("$PYTHONEXE")
            python_dir = os.path.dirname(python_exe)
            uv_exe_suffix = ".exe" if IS_WINDOWS else ""
            uv_cmd = str(Path(python_dir) / f"uv{uv_exe_suffix}")
            if not os.path.isfile(uv_cmd):
                uv_cmd = "uv"
            subprocess.check_call(
                [uv_cmd, "venv", "--clear", f"--python={python_exe}", penv_dir],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            uv_success = True
            print(f"Created Python virtualenv using uv: {penv_dir}")
        except Exception:
            pass
        if not uv_success:
            uv_cmd = None
            env.Execute(env.VerboseAction(
                '"$PYTHONEXE" -m venv --clear "%s"' % penv_dir,
                "Created Python virtualenv: %s" % penv_dir))
        penv_python = get_executable_path(penv_dir, "python")
        if not os.path.isfile(penv_python):
            sys.stderr.write(
                f"Error: Failed to create a proper virtual environment. "
                f"Missing the `python` binary at {penv_python}! Created with uv: {uv_success}\n")
            sys.exit(1)
        Path(penv_dir, "pioarduino_py").write_text("required by pioarduino\n")
        return uv_cmd if uv_success else None
    return None

def _setup_pipenv_minimal(penv_dir):
    """
    Ensure a Python virtual environment exists at `penv_dir`.
    If the marker file 'pioarduino_py' is missing or the virtual environment is broken,
    delete penv_dir and recreate using uv. Write the marker file afterward.

    Args:
        penv_dir (str): Virtual environment directory path.

    Returns:
        str or None: 'uv' if created with uv, else None
    """
    penv_dir_path = Path(penv_dir)
    marker_file = penv_dir_path / "pioarduino_py"
    penv_python = get_executable_path(penv_dir, "python")
    if not marker_file.is_file() or not os.path.isfile(penv_python):
        print("Marker file missing or penv corrupted, removing and recreating using uv (minimal setup).")
        if penv_dir_path.exists():
            shutil.rmtree(penv_dir_path)
        uv_cmd = "uv"
        python_exe = sys.executable
        try:
            subprocess.check_call([
                uv_cmd, "venv", "--clear", f"--python={python_exe}", str(penv_dir)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            marker_file.write_text("required by pioarduino\n")
            print(f"Created Python virtual environment using uv (minimal): {penv_dir}")
            return uv_cmd
        except Exception as exc:
            print(f"Error: Failed to create penv with uv (minimal): {exc}")
            print("Trying fallback to python -m venv...")
        try:
            subprocess.check_call([
                python_exe, "-m", "venv", "--clear", str(penv_dir)
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            marker_file.write_text("required by pioarduino\n")
            print(f"Created Python virtual environment using python -m venv (minimal): {penv_dir}")
            return None
        except Exception as exc:
            sys.stderr.write(f"Error: Failed to create minimal virtual environment: {exc}\n")
            sys.exit(1)
    return None

def setup_python_paths(penv_dir):
    """
    Add penv's site-packages to Python's module search path.

    Args:
        penv_dir (str): Path to the penv directory
    """
    python_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = (
        str(Path(penv_dir) / "Lib" / "site-packages") if IS_WINDOWS
        else str(Path(penv_dir) / "lib" / python_ver / "site-packages")
    )
    if os.path.isdir(site_packages):
        site.addsitedir(site_packages)

def get_packages_to_install(deps, installed_packages):
    """
    Yield needed packages to install, based on the required `deps` and currently installed packages.

    Args:
        deps (dict): Required {package: version_spec} mapping
        installed_packages (dict): {name_lower: version}

    Yields:
        str: Package name that must be installed or updated
    """
    for package, spec in deps.items():
        name = package.lower()
        if name not in installed_packages:
            yield package
        elif name == "platformio":
            m = PLATFORMIO_URL_VERSION_RE.search(spec)
            if m:
                expected_ver = pepver_to_semver(m.group(1))
                if installed_packages.get(name) != expected_ver:
                    yield package
            else:
                continue
        else:
            version_spec = semantic_version.SimpleSpec(spec)
            if not version_spec.match(installed_packages[name]):
                yield package

def install_python_deps(python_exe, external_uv_executable):
    """
    Ensure all required Python dependencies are installed in penv using uv package manager.

    Args:
        python_exe: Path to Python inside penv
        external_uv_executable: uv executable path (may be None)

    Returns:
        bool: True on success, False otherwise
    """
    penv_dir = os.path.dirname(os.path.dirname(python_exe))
    penv_uv_executable = get_executable_path(penv_dir, "uv")
    uv_in_penv_available = False
    try:
        result = subprocess.run(
            [penv_uv_executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10)
        uv_in_penv_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        uv_in_penv_available = False

    # Install uv into penv if missing
    if not uv_in_penv_available:
        if external_uv_executable:
            try:
                subprocess.check_call(
                    [external_uv_executable, "pip", "install", "uv>=0.1.0", f"--python={python_exe}", "--quiet"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=300)
            except Exception as e:
                print(f"Error installing uv package manager into penv: {e}")
                return False
        else:
            try:
                subprocess.check_call(
                    [python_exe, "-m", "pip", "install", "uv>=0.1.0", "--quiet"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=300)
            except Exception as e:
                print(f"Error installing uv package manager via pip: {e}")
                return False

    def _get_installed_uv_packages():
        """
        Retrieve dict of installed packages and versions using uv in penv.
        """
        result = {}
        try:
            cmd = [penv_uv_executable, "pip", "list", f"--python={python_exe}", "--format=json"]
            result_obj = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=300)
            if result_obj.returncode == 0 and result_obj.stdout.strip():
                packages = json.loads(result_obj.stdout.strip())
                for p in packages:
                    result[p["name"].lower()] = pepver_to_semver(p["version"])
        except Exception as e:
            print(f"Couldn't extract Python package list: {e}")
        return result

    installed_packages = _get_installed_uv_packages()
    packages_to_install = list(get_packages_to_install(python_deps, installed_packages))
    if packages_to_install:
        packages_list = []
        for p in packages_to_install:
            spec = python_deps[p]
            if spec.startswith(('http://', 'https://', 'git+', 'file://')):
                packages_list.append(spec)
            else:
                packages_list.append(f"{p}{spec}")
        cmd = [
            penv_uv_executable, "pip", "install",
            f"--python={python_exe}",
            "--quiet", "--upgrade"
        ] + packages_list
        try:
            subprocess.check_call(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, timeout=300)
        except Exception as e:
            print(f"Error installing Python dependencies: {e}")
            return False
    return True

def install_esptool(env, platform, python_exe, uv_executable):
    """
    Install esptool from provided tool-esptoolpy package directory.

    Args:
        env: SCons environment
        platform: PlatformIO platform object
        python_exe: Path to Python inside penv
        uv_executable: Path to uv inside penv
    """
    esptool_repo_path = platform.get_package_dir("tool-esptoolpy") or ""
    if not esptool_repo_path or not os.path.isdir(esptool_repo_path):
        sys.stderr.write(f"Error: 'tool-esptoolpy' package directory not found: {esptool_repo_path!r}\n")
        sys.exit(1)
    try:
        result = subprocess.run(
            [python_exe, "-c",
             ("import esptool, os, sys; "
              "expected_path = os.path.normcase(os.path.realpath(sys.argv[1])); "
              "actual_path = os.path.normcase(os.path.realpath(os.path.dirname(esptool.__file__))); "
              "print('MATCH' if actual_path.startswith(expected_path) else 'MISMATCH')")
             , esptool_repo_path], capture_output=True, check=True, text=True, timeout=5)
        if result.stdout.strip() == "MATCH":
            return
    except Exception:
        pass
    try:
        subprocess.check_call([
            uv_executable, "pip", "install", "--quiet", "--force-reinstall",
            f"--python={python_exe}", "-e", esptool_repo_path], timeout=60)
    except Exception as e:
        sys.stderr.write(
            f"Error: Failed to install esptool from {esptool_repo_path} ({str(e)})\n")
        sys.exit(1)

def setup_penv_minimal(platform, platformio_dir: str, install_esptool: bool = True):
    """
    Set up Python penv in minimal mode (no SCons). Installs dependencies and optional esptool.

    Args:
        platform: PlatformIO platform object
        platformio_dir (str): PlatformIO root directory
        install_esptool (bool): Whether to install esptool

    Returns:
        tuple: (Path to penv Python binary, path to esptool script)
    """
    return _setup_python_environment_core(None, platform, platformio_dir, should_install_esptool=install_esptool)

def _setup_python_environment_core(env, platform, platformio_dir, should_install_esptool=True):
    """
    Shared Python environment setup logic for both SCons and minimal variants.

    Args:
        env: SCons environment (or None)
        platform: PlatformIO platform object
        platformio_dir: Root directory of PlatformIO install
        should_install_esptool (bool): Whether to install esptool

    Returns:
        tuple: (Penv python path, esptool executable path)
    """
    penv_dir = ensure_penv_with_uv(platformio_dir) if env is not None else _setup_pipenv_minimal(str(Path(platformio_dir) / "penv"))
    penv_python = get_executable_path(penv_dir, "python")
    if env is not None:
        env.Replace(PYTHONEXE=penv_python)
    if not os.path.isfile(penv_python):
        sys.stderr.write(f"Error: Python executable not found: {penv_python}\n")
        sys.exit(1)
    setup_python_paths(penv_dir)
    esptool_binary_path = get_executable_path(penv_dir, "esptool")
    uv_executable = get_executable_path(penv_dir, "uv")
    if has_internet_connection() or github_actions:
        if not install_python_deps(penv_python, uv_executable):
            sys.stderr.write("Error: Failed to install Python dependencies into penv\n")
            sys.exit(1)
    else:
        print("Warning: No internet connection detected, Python dependency check will be skipped.")
    if should_install_esptool:
        if env is not None:
            install_esptool(env, platform, penv_python, uv_executable)
        else:
            _install_esptool_from_tl_install(platform, penv_python, uv_executable)
    _setup_certifi_env(env, penv_python)
    return penv_python, esptool_binary_path

def _install_esptool_from_tl_install(platform, python_exe, uv_executable):
    """
    Install esptool from tl-install-provided directory to penv.

    Args:
        platform: PlatformIO platform object
        python_exe: penv python executable path
        uv_executable: penv uv executable path
    """
    esptool_repo_path = platform.get_package_dir("tool-esptoolpy") or ""
    if not esptool_repo_path or not os.path.isdir(esptool_repo_path):
        return
    try:
        result = subprocess.run(
            [python_exe, "-c",
             ("import esptool, os, sys; "
              "expected_path = os.path.normcase(os.path.realpath(sys.argv[1])); "
              "actual_path = os.path.normcase(os.path.realpath(os.path.dirname(esptool.__file__))); "
              "print('MATCH' if actual_path.startswith(expected_path) else 'MISMATCH')")
             , esptool_repo_path], capture_output=True, check=True, text=True, timeout=5)
        if result.stdout.strip() == "MATCH":
            return
    except Exception:
        pass
    try:
        subprocess.check_call([
            uv_executable, "pip", "install", "--quiet", "--force-reinstall",
            f"--python={python_exe}", "-e", esptool_repo_path], timeout=60)
        print(f"Installed esptool from tl-install path: {esptool_repo_path}")
    except Exception as e:
        print(f"Warning: Failed to install esptool from {esptool_repo_path} ({str(e)})")

def _setup_certifi_env(env, python_exe):
    """
    Set up certifi-based environment variables based on penv Python.

    Args:
        env: SCons environment or None
        python_exe: Python executable from penv
    """
    try:
        out = subprocess.check_output(
            [python_exe, "-c", "import certifi; print(certifi.where())"],
            text=True, timeout=5)
        cert_path = out.strip()
    except Exception as e:
        print(f"Error: Failed to obtain certifi path from the virtual environment: {e}")
        return
    os.environ["CERTIFI_PATH"] = cert_path
    os.environ["SSL_CERT_FILE"] = cert_path
    os.environ["REQUESTS_CA_BUNDLE"] = cert_path
    os.environ["CURL_CA_BUNDLE"] = cert_path
    os.environ["GIT_SSL_CAINFO"] = cert_path
    if env is not None:
        env_vars = dict(env.get("ENV", {}))
        env_vars.update({
            "CERTIFI_PATH": cert_path,
            "SSL_CERT_FILE": cert_path,
            "REQUESTS_CA_BUNDLE": cert_path,
            "CURL_CA_BUNDLE": cert_path,
            "GIT_SSL_CAINFO": cert_path,
        })
        env.Replace(ENV=env_vars)

def setup_python_environment(env, platform, platformio_dir):
    """
    SCons-mode setup for Python penv and dependencies.

    Args:
        env: SCons environment object
        platform: PlatformIO platform object
        platformio_dir (str): Path to PlatformIO core dir

    Returns:
        tuple: (penv python path, esptool path)
    """
    return _setup_python_environment_core(env, platform, platformio_dir, should_install_esptool=True)
