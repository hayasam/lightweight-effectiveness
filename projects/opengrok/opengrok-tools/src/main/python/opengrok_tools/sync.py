#!/usr/bin/env python3
#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# See LICENSE.txt included in this distribution for the specific
# language governing permissions and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at LICENSE.txt.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#

#
# Copyright (c) 2017, 2018, Oracle and/or its affiliates. All rights reserved.
#

"""
 This script runs OpenGrok parallel project processing.

 It is intended to work on Unix systems. (mainly because it relies on the
 OpenGrok shell script - for the time being)

"""

import argparse
import logging
import multiprocessing
import os
import sys
import tempfile
from multiprocessing import Pool
from os import path

from .utils.commandsequence import CommandSequence, CommandSequenceBase
from .utils.filelock import Timeout, FileLock
from .utils.opengrok import list_indexed_projects, get_config_value
from .utils.readconfig import read_config
from .utils.utils import is_web_uri
from .utils.log import get_console_logger

major_version = sys.version_info[0]
if (major_version < 3):
    print("Need Python 3, you are running {}".format(major_version))
    sys.exit(1)

__version__ = "0.6"


def worker(base):
    """
    Process one project by calling set of commands.
    """

    x = CommandSequence(base)
    x.run()
    base.fill(x.retcodes, x.outputs, x.failed)

    return base


def main():
    dirs_to_process = []

    parser = argparse.ArgumentParser(description='Manage parallel workers.')
    parser.add_argument('-w', '--workers', default=multiprocessing.cpu_count(),
                        help='Number of worker processes')

    # There can be only one way how to supply list of projects to process.
    group1 = parser.add_mutually_exclusive_group()
    group1.add_argument('-d', '--directory',
                        help='Directory to process')
    group1.add_argument('-P', '--projects', nargs='*',
                        help='List of projects to process')
    parser.add_argument('-I', '--indexed', action='store_true',
                        help='Sync indexed projects only')
    parser.add_argument('-D', '--debug', action='store_true',
                        help='Enable debug prints')
    parser.add_argument('-i', '--ignore_errors', nargs='*',
                        help='ignore errors from these projects')
    parser.add_argument('-c', '--config', required=True,
                        help='config file in JSON format')
    parser.add_argument('-U', '--uri', default='http://localhost:8080/source',
                        help='URI of the webapp with context path')
    args = parser.parse_args()

    loglevel = logging.INFO
    if args.debug:
        loglevel = logging.DEBUG
    logger = get_console_logger(__name__, loglevel)

    uri = args.uri
    if not is_web_uri(uri):
        logger.error("Not a URI: {}".format(uri))
        sys.exit(1)
    logger.debug("web application URI = {}".format(uri))

    # Changing working directory to root will avoid problems when running
    # programs via sudo/su.
    try:
        os.chdir("/")
    except OSError:
        logger.error("cannot change working directory to /",
                     exc_info=True)
        sys.exit(1)

    # First read and validate configuration file as it is mandatory argument.
    config = read_config(logger, args.config)
    if config is None:
        logger.error("Cannot read config file from {}".format(args.config))
        sys.exit(1)

    try:
        commands = config["commands"]
    except KeyError:
        logger.error("The config file has to contain key \"commands\"")
        sys.exit(1)

    directory = args.directory
    if not args.directory and not args.projects and not args.indexed:
        # Assume directory, get the source root value from the webapp.
        directory = get_config_value(logger, 'sourceRoot', uri)
        if not directory:
            logger.error("Neither -d or -P or -I specified and cannot get "
                         "source root from the webapp")
            sys.exit(1)
        else:
            logger.info("Assuming directory: {}".format(directory))

    ignore_errors = []
    if args.ignore_errors:
        ignore_errors = args.ignore_errors
    else:
        try:
            ignore_errors = config["ignore_errors"]
        except KeyError:
            pass
    logger.debug("Ignored projects: {}".format(ignore_errors))

    lock = FileLock(os.path.join(tempfile.gettempdir(),
                                 "opengrok-sync.lock"))
    try:
        with lock.acquire(timeout=0):
            pool = Pool(processes=int(args.workers))

            if args.projects:
                dirs_to_process = args.projects
                logger.debug("Processing directories: {}".
                             format(dirs_to_process))
            elif args.indexed:
                indexed_projects = list_indexed_projects(logger, uri)
                logger.debug("Processing indexed projects: {}".
                             format(indexed_projects))

                if indexed_projects:
                    for line in indexed_projects:
                        dirs_to_process.append(line.strip())
                else:
                    logger.error("cannot get list of projects")
                    sys.exit(1)
            else:
                logger.debug("Processing directory {}".format(directory))
                for entry in os.listdir(directory):
                    if path.isdir(path.join(directory, entry)):
                        dirs_to_process.append(entry)

            logger.debug("to process: {}".format(dirs_to_process))

            cmds_base = []
            for d in dirs_to_process:
                cmd_base = CommandSequenceBase(d, commands, loglevel,
                                               config.get("cleanup"))
                cmds_base.append(cmd_base)

            # Map the commands into pool of workers so they can be processed.
            try:
                cmds_base_results = pool.map(worker, cmds_base, 1)
            except KeyboardInterrupt:
                # XXX lock.release() or return 1 ?
                sys.exit(1)
            else:
                for cmds_base in cmds_base_results:
                    logger.debug("Checking results of project {}".
                                 format(cmds_base))
                    cmds = CommandSequence(cmds_base)
                    cmds.fill(cmds_base.retcodes, cmds_base.outputs,
                              cmds_base.failed)
                    cmds.check(ignore_errors)
    except Timeout:
        logger.warning("Already running, exiting.")
        sys.exit(1)


if __name__ == '__main__':
    main()
