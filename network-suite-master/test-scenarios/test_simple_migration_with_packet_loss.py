#
# Copyright oVirt Authors
# SPDX-License-Identifier: GPL-2.0-or-later
#
#
"""
Simple example: Test VM migration with packet loss between engine and host.

This is a minimal example showing how to test VM migration while
simulating network connectivity issues.
"""

import contextlib
import logging
import time

import pytest

from fixtures.host import ETH1

from ovirtlib import virtlib
from ovirtlib import netattachlib
from ovirtlib import netlib
from ovirtlib import clusterlib
from ovirtlib import hostlib
from ovirtlib import joblib
from ovirtlib import templatelib

LOGGER = logging.getLogger(__name__)


@contextlib.contextmanager
def simulate_packet_loss(ansible_host, target_ip, loss_percent=30):
    """
    Temporarily add packet loss to a host.

    Args:
        ansible_host: The host to apply packet loss on
        target_ip: IP address to drop packets to (usually engine IP)
        loss_percent: Percentage of packets to drop (0-100)
    """
    rule = (
        f'iptables -A OUTPUT -d {target_ip} -m statistic ' f'--mode random --probability 0.{loss_percent:02d} -j DROP'
    )

    try:
        LOGGER.info(f"Adding {loss_percent}% packet loss to {target_ip}")
        ansible_host.shell(rule)
        yield
    finally:
        LOGGER.info("Removing packet loss rule")
        try:
            # Remove the rule (change -A to -D)
            ansible_host.shell(rule.replace('-A OUTPUT', '-D OUTPUT'))
        except Exception as e:
            LOGGER.warning(f"Failed to remove iptables rule: {e}")


def test_simple_migration_with_packet_loss(
    system,
    default_cluster,
    default_storage_domain,
    ovirtmgmt_vnic_profile,
    host_0_up,
    host_1_up,
    ansible_host0,
    ansible_engine_facts,
):
    """
    Simple test: Migrate VM while source host has 30% packet loss to engine.
    """
    # Get engine IP
    engine_ip = ansible_engine_facts.get_all()['ansible_default_ipv4']['address']

    # Create a simple VM
    disk = default_storage_domain.create_disk('simple_test_disk')
    with virtlib.vm_pool(system, size=1) as (vm,):
        vm.create(
            vm_name='simple_migration_test_vm',
            cluster=default_cluster,
            template=templatelib.TEMPLATE_BLANK,
        )
        vm.create_vnic('nic0', ovirtmgmt_vnic_profile)
        disk_att_id = vm.attach_disk(disk=disk)
        vm.wait_for_disk_up_status(disk, disk_att_id)

        # Start the VM
        vm.run()
        vm.wait_for_up_status()
        joblib.AllJobs(system).wait_for_done()

        # Ensure VM is on host_0
        if vm.host.id != host_0_up.id:
            vm.migrate(host_0_up.name)
            vm.wait_for_up_status()

        LOGGER.info(f"VM is running on {host_0_up.name}")

        # Simulate packet loss on host_0 (source host)
        with simulate_packet_loss(ansible_host0, engine_ip, loss_percent=30):
            time.sleep(2)  # Let the rule take effect

            # Migrate to host_1
            LOGGER.info(f"Starting migration to {host_1_up.name} with 30% packet loss")
            vm.migrate(host_1_up.name)

            # Wait for migration to complete (may take longer due to packet loss)
            vm.wait_for_up_status()

        # Verify migration succeeded
        assert vm.host.id == host_1_up.id
        LOGGER.info("✓ Migration succeeded despite 30% packet loss!")
