#
# Copyright oVirt Authors
# SPDX-License-Identifier: GPL-2.0-or-later
#
#
"""
Test VM migration when there are connectivity issues between engine and hosts.

This test simulates network issues by using iptables to temporarily drop
packets between the engine and hosts during VM migration operations.
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

VM_NAME = 'test_migration_with_issues_vm'
MIG_NET = 'mig-net-issues'
NIC_NAME = 'nic0'

# Static IP assignments for migration network
STATIC_ASSIGN_1 = {
    'inet': netattachlib.StaticIpv4Assignment('192.0.4.1', '255.255.255.0'),
    'inet6': netattachlib.StaticIpv6Assignment('fd8f:192:0:4::1', '64'),
}
STATIC_ASSIGN_2 = {
    'inet': netattachlib.StaticIpv4Assignment('192.0.4.2', '255.255.255.0'),
    'inet6': netattachlib.StaticIpv6Assignment('fd8f:192:0:4::2', '64'),
}

# Network disruption parameters
PACKET_LOSS_PERCENTAGE = 30  # Percentage of packets to drop
LATENCY_MS = 100  # Additional latency in milliseconds


@pytest.fixture
def migration_network(default_data_center, default_cluster):
    """Create a dedicated migration network."""
    network = netlib.Network(default_data_center)
    network.create(name=MIG_NET, usages=())
    cluster_network = clusterlib.ClusterNetwork(default_cluster)
    cluster_network.assign(network)
    cluster_network.set_usages([netlib.NetworkUsage.MIGRATION])
    yield network
    network.remove()


@pytest.fixture
def host_0_with_mig_net(migration_network, host_0_up, af):
    """Setup host 0 with migration network."""
    mig_att_data = netattachlib.NetworkAttachmentData(migration_network, ETH1, (STATIC_ASSIGN_1[af.family],))
    host_0_up.setup_networks([mig_att_data])
    yield host_0_up
    host_0_up.remove_networks((migration_network,))


@pytest.fixture
def host_1_with_mig_net(migration_network, host_1_up, af):
    """Setup host 1 with migration network."""
    mig_att_data = netattachlib.NetworkAttachmentData(migration_network, ETH1, (STATIC_ASSIGN_2[af.family],))
    host_1_up.setup_networks([mig_att_data])
    yield host_1_up
    host_1_up.remove_networks((migration_network,))


@pytest.fixture
def running_vm(
    system,
    default_cluster,
    default_storage_domain,
    ovirtmgmt_vnic_profile,
    host_0_up,
    host_1_up,
):
    """Create and run a VM for migration testing."""
    disk = default_storage_domain.create_disk('disk_migration_test')
    with virtlib.vm_pool(system, size=1) as (vm,):
        vm.create(
            vm_name=VM_NAME,
            cluster=default_cluster,
            template=templatelib.TEMPLATE_BLANK,
        )
        vm.create_vnic(NIC_NAME, ovirtmgmt_vnic_profile)
        disk_att_id = vm.attach_disk(disk=disk)
        vm.wait_for_disk_up_status(disk, disk_att_id)
        vm.run()
        vm.wait_for_up_status()
        joblib.AllJobs(system).wait_for_done()
        yield vm


@contextlib.contextmanager
def network_disruption_on_host(ansible_host, target_ip, disruption_type='packet_loss'):
    """
    Context manager to temporarily disrupt network connectivity.

    Args:
        ansible_host: Ansible host object
        target_ip: IP address to disrupt connectivity to (engine IP)
        disruption_type: Type of disruption ('packet_loss', 'latency', or 'both')
    """
    LOGGER.info(f"Setting up network disruption on {ansible_host} to {target_ip}")

    try:
        if disruption_type in ['packet_loss', 'both']:
            # Use iptables to drop packets randomly
            # Note: This drops packets TO the engine, simulating unreliable connectivity
            cmd = (
                f'iptables -A OUTPUT -d {target_ip} -m statistic '
                f'--mode random --probability 0.{PACKET_LOSS_PERCENTAGE:02d} '
                f'-j DROP'
            )
            ansible_host.shell(cmd)
            LOGGER.info(f"Applied {PACKET_LOSS_PERCENTAGE}% packet loss rule")

        if disruption_type in ['latency', 'both']:
            # Use tc (traffic control) to add network latency
            # First check if tc qdisc already exists
            ansible_host.shell(f'tc qdisc add dev eth0 root netem delay {LATENCY_MS}ms 10ms')
            LOGGER.info(f"Applied {LATENCY_MS}ms latency")

        yield

    finally:
        # Clean up iptables rules
        LOGGER.info("Cleaning up network disruption rules")
        try:
            if disruption_type in ['packet_loss', 'both']:
                cmd = (
                    f'iptables -D OUTPUT -d {target_ip} -m statistic '
                    f'--mode random --probability 0.{PACKET_LOSS_PERCENTAGE:02d} '
                    f'-j DROP'
                )
                ansible_host.shell(cmd)
        except Exception as e:
            LOGGER.warning(f"Failed to remove iptables rule: {e}")

        try:
            if disruption_type in ['latency', 'both']:
                ansible_host.shell('tc qdisc del dev eth0 root netem')
        except Exception as e:
            LOGGER.warning(f"Failed to remove tc qdisc rule: {e}")


def test_vm_migration_with_packet_loss_to_source_host(
    running_vm,
    host_0_with_mig_net,
    host_1_with_mig_net,
    ansible_host0,
    ansible_host1,
    ansible_engine_facts,
):
    """
    Test VM migration when source host has packet loss to engine.

    This simulates a scenario where the host running the VM has unreliable
    connectivity to the engine, which might happen during network issues.
    The migration should still succeed despite the packet loss.
    """
    # Determine source and destination hosts
    if running_vm.host.id == host_0_with_mig_net.id:
        src_host = host_0_with_mig_net
        dst_host = host_1_with_mig_net
        ansible_src = ansible_host0
    else:
        src_host = host_1_with_mig_net
        dst_host = host_0_with_mig_net
        ansible_src = ansible_host1

    engine_ip = ansible_engine_facts.get_all()['ansible_default_ipv4']['address']

    LOGGER.info(f"Starting migration from {src_host.name} to {dst_host.name}")
    LOGGER.info(f"Applying packet loss between source host and engine ({engine_ip})")

    # Apply network disruption during migration
    with network_disruption_on_host(ansible_src, engine_ip, disruption_type='packet_loss'):
        # Give some time for disruption to be applied
        time.sleep(2)

        # Initiate migration
        running_vm.migrate(dst_host.name)

        # Wait for migration to complete
        # Migration might take longer due to packet loss
        running_vm.wait_for_up_status()

    # Verify migration succeeded
    assert running_vm.host.id == dst_host.id
    LOGGER.info("Migration completed successfully despite packet loss")


def test_vm_migration_with_latency_to_destination_host(
    running_vm,
    host_0_with_mig_net,
    host_1_with_mig_net,
    ansible_host0,
    ansible_host1,
    ansible_engine_facts,
):
    """
    Test VM migration when destination host has high latency to engine.

    This simulates a scenario where the destination host has slow
    connectivity to the engine during migration.
    """
    # Determine source and destination hosts
    if running_vm.host.id == host_0_with_mig_net.id:
        src_host = host_0_with_mig_net
        dst_host = host_1_with_mig_net
        ansible_dst = ansible_host1
    else:
        src_host = host_1_with_mig_net
        dst_host = host_0_with_mig_net
        ansible_dst = ansible_host0

    engine_ip = ansible_engine_facts.get_all()['ansible_default_ipv4']['address']

    LOGGER.info(f"Starting migration from {src_host.name} to {dst_host.name}")
    LOGGER.info(f"Applying latency between destination host and engine ({engine_ip})")

    # Apply network latency on destination host
    with network_disruption_on_host(ansible_dst, engine_ip, disruption_type='latency'):
        # Give some time for disruption to be applied
        time.sleep(2)

        # Initiate migration
        running_vm.migrate(dst_host.name)

        # Wait for migration to complete
        running_vm.wait_for_up_status()

    # Verify migration succeeded
    assert running_vm.host.id == dst_host.id
    LOGGER.info("Migration completed successfully despite latency")


def test_vm_migration_with_intermittent_connectivity(
    running_vm,
    host_0_with_mig_net,
    host_1_with_mig_net,
    ansible_host0,
    ansible_host1,
    ansible_engine_facts,
):
    """
    Test VM migration when both hosts have intermittent connectivity to engine.

    This is a more realistic scenario where network issues affect both
    the source and destination hosts during migration.
    """
    # Determine source and destination hosts
    if running_vm.host.id == host_0_with_mig_net.id:
        src_host = host_0_with_mig_net
        dst_host = host_1_with_mig_net
        ansible_src = ansible_host0
        ansible_dst = ansible_host1
    else:
        src_host = host_1_with_mig_net
        dst_host = host_0_with_mig_net
        ansible_src = ansible_host1
        ansible_dst = ansible_host0

    engine_ip = ansible_engine_facts.get_all()['ansible_default_ipv4']['address']

    LOGGER.info(f"Starting migration from {src_host.name} to {dst_host.name}")
    LOGGER.info(f"Applying packet loss on both hosts to engine ({engine_ip})")

    # Apply network disruption on both hosts
    with network_disruption_on_host(ansible_src, engine_ip, disruption_type='both'):
        with network_disruption_on_host(ansible_dst, engine_ip, disruption_type='packet_loss'):
            # Give some time for disruption to be applied
            time.sleep(2)

            # Initiate migration
            running_vm.migrate(dst_host.name)

            # Wait for migration to complete
            # This might take significantly longer
            running_vm.wait_for_up_status()

    # Verify migration succeeded
    assert running_vm.host.id == dst_host.id
    LOGGER.info("Migration completed successfully despite network issues on both hosts")


def test_vm_stays_running_during_brief_engine_host_connectivity_loss(
    running_vm,
    host_0_with_mig_net,
    ansible_host0,
    ansible_engine_facts,
):
    """
    Test that a running VM stays up when there's brief connectivity loss
    between engine and host.

    This verifies the resilience of the system to transient network issues.
    """
    engine_ip = ansible_engine_facts.get_all()['ansible_default_ipv4']['address']

    # Ensure VM is on host_0
    if running_vm.host.id != host_0_with_mig_net.id:
        running_vm.migrate(host_0_with_mig_net.name)
        running_vm.wait_for_up_status()

    initial_status = running_vm.status
    LOGGER.info(f"VM initial status: {initial_status}")

    # Apply brief network disruption
    LOGGER.info("Applying brief network disruption...")
    with network_disruption_on_host(ansible_host0, engine_ip, disruption_type='both'):
        time.sleep(10)  # 10 seconds of disruption

    # Allow some time for recovery
    time.sleep(5)

    # VM should still be running
    current_status = running_vm.status
    LOGGER.info(f"VM status after disruption: {current_status}")
    assert str(current_status) == 'up', f"VM should still be up, but status is {current_status}"
    LOGGER.info("VM remained running despite brief connectivity loss")
