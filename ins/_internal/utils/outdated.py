from __future__ import absolute_import

import datetime
import json
import logging
import os.path
import sys

from ins._vendor import lockfile, pkg_resources
from ins._vendor.packaging import version as packaging_version

from ins._internal.index import PackageFinder
from ins._internal.utils.compat import WINDOWS
from ins._internal.utils.filesystem import check_path_owner
from ins._internal.utils.misc import ensure_dir, get_installed_version
from ins._internal.utils.typing import MYPY_CHECK_RUNNING

if MYPY_CHECK_RUNNING:
    import optparse
    from typing import Any, Dict
    from ins._internal.download import insSession


SELFCHECK_DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"


logger = logging.getLogger(__name__)


class SelfCheckState(object):
    def __init__(self, cache_dir):
        # type: (str) -> None
        self.state = {}  # type: Dict[str, Any]
        self.statefile_path = None

        # Try to load the existing state
        if cache_dir:
            self.statefile_path = os.path.join(cache_dir, "selfcheck.json")
            try:
                with open(self.statefile_path) as statefile:
                    self.state = json.load(statefile)[sys.prefix]
            except (IOError, ValueError, KeyError):
                # Explicitly suppressing exceptions, since we don't want to
                # error out if the cache file is invalid.
                pass

    def save(self, pypi_version, current_time):
        # type: (str, datetime.datetime) -> None
        # If we do not have a path to cache in, don't bother saving.
        if not self.statefile_path:
            return

        # Check to make sure that we own the directory
        if not check_path_owner(os.path.dirname(self.statefile_path)):
            return

        # Now that we've ensured the directory is owned by this user, we'll go
        # ahead and make sure that all our directories are created.
        ensure_dir(os.path.dirname(self.statefile_path))

        # Attempt to write out our version check file
        with lockfile.LockFile(self.statefile_path):
            if os.path.exists(self.statefile_path):
                with open(self.statefile_path) as statefile:
                    state = json.load(statefile)
            else:
                state = {}

            state[sys.prefix] = {
                "last_check": current_time.strftime(SELFCHECK_DATE_FMT),
                "pypi_version": pypi_version,
            }

            with open(self.statefile_path, "w") as statefile:
                json.dump(state, statefile, sort_keys=True,
                          separators=(",", ":"))


def was_installed_by_ins(pkg):
    # type: (str) -> bool
    """Checks whether pkg was installed by ins

    This is used not to display the upgrade message when ins is in fact
    installed by system package manager, such as dnf on Fedora.
    """
    try:
        dist = pkg_resources.get_distribution(pkg)
        return (dist.has_metadata('INSTALLER') and
                'ins' in dist.get_metadata_lines('INSTALLER'))
    except pkg_resources.DistributionNotFound:
        return False


def ins_version_check(session, options):
    # type: (insSession, optparse.Values) -> None
    """Check for an update for ins.

    Limit the frequency of checks to once per week. State is stored either in
    the active virtualenv or in the user's USER_CACHE_DIR keyed off the prefix
    of the ins script path.
    """
    installed_version = get_installed_version("ins")
    if not installed_version:
        return

    ins_version = packaging_version.parse(installed_version)
    pypi_version = None

    try:
        state = SelfCheckState(cache_dir=options.cache_dir)

        current_time = datetime.datetime.utcnow()
        # Determine if we need to refresh the state
        if "last_check" in state.state and "pypi_version" in state.state:
            last_check = datetime.datetime.strptime(
                state.state["last_check"],
                SELFCHECK_DATE_FMT
            )
            if (current_time - last_check).total_seconds() < 7 * 24 * 60 * 60:
                pypi_version = state.state["pypi_version"]

        # Refresh the version if we need to or just see if we need to warn
        if pypi_version is None:
            # Lets use PackageFinder to see what the latest ins version is
            finder = PackageFinder(
                find_links=options.find_links,
                index_urls=[options.index_url] + options.extra_index_urls,
                allow_all_prereleases=False,  # Explicitly set to False
                trusted_hosts=options.trusted_hosts,
                session=session,
            )
            candidate = finder.find_candidates("ins").get_best()
            if candidate is None:
                return
            pypi_version = str(candidate.version)

            # save that we've performed a check
            state.save(pypi_version, current_time)

        remote_version = packaging_version.parse(pypi_version)

        # Determine if our pypi_version is older
        if (ins_version < remote_version and
                ins_version.base_version != remote_version.base_version and
                was_installed_by_ins('ins')):
            # Advise "python -m ins" on Windows to avoid issues
            # with overwriting ins.exe.
            if WINDOWS:
                ins_cmd = "python -m ins"
            else:
                ins_cmd = "ins"
            logger.warning(
                "You are using ins version %s, however version %s is "
                "available.\nYou should consider upgrading via the "
                "'%s install --upgrade ins' command.",
                ins_version, pypi_version, ins_cmd
            )
    except Exception:
        logger.debug(
            "There was an error checking the latest version of ins",
            exc_info=True,
        )
