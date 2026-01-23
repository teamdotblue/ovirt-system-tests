# VM Migration with Connectivity Issues Test

## Overview

This test suite (`test_vm_migration_with_connectivity_issues.py`) validates VM migration behavior when there are network connectivity issues between the oVirt Engine and the hosts.

## What It Tests

The test simulates real-world network problems that can occur in production environments:

1. **Packet Loss to Source Host** - Tests VM migration when the source host has unreliable connectivity (30% packet loss) to the engine
2. **Latency to Destination Host** - Tests VM migration when the destination host has high latency (100ms) to the engine
3. **Intermittent Connectivity** - Tests VM migration when both hosts experience network issues simultaneously
4. **VM Resilience** - Verifies that VMs remain running during brief engine-host connectivity loss

## Network Disruption Techniques

The tests use two methods to simulate network issues:

### 1. iptables (Packet Loss)
```bash
iptables -A OUTPUT -d <engine_ip> -m statistic --mode random --probability 0.30 -j DROP
```
This randomly drops 30% of packets going to the engine, simulating unreliable network conditions.

### 2. tc (Traffic Control - Latency)
```bash
tc qdisc add dev eth0 root netem delay 100ms 10ms
```
This adds 100ms ± 10ms of latency to network traffic, simulating slow network conditions.

## Prerequisites

- Network suite test environment with at least 2 hosts
- Hosts must have `iptables` and `tc` (iproute2) available
- Engine and hosts should be in a healthy state before running tests

## Running the Tests

### Run all connectivity issue tests:
```bash
cd network-suite-master
pytest test-scenarios/test_vm_migration_with_connectivity_issues.py -v
```

### Run a specific test:
```bash
pytest test-scenarios/test_vm_migration_with_connectivity_issues.py::test_vm_migration_with_packet_loss_to_source_host -v
```

### Run with IPv6:
```bash
TESTED_IP_VERSION=6 pytest test-scenarios/test_vm_migration_with_connectivity_issues.py -v
```

## Test Parameters

You can customize the disruption parameters by modifying the constants in the test file:

- `PACKET_LOSS_PERCENTAGE` - Default: 30 (30% packet loss)
- `LATENCY_MS` - Default: 100 (100ms latency)

## Expected Behavior

Despite network issues, the tests expect:
- ✅ VM migrations should complete successfully (may take longer)
- ✅ VMs should remain in 'up' status
- ✅ Network disruption rules are properly cleaned up after each test

## Cleanup

The tests use context managers to ensure network disruption rules are removed even if the test fails. However, if a test is forcefully interrupted, you may need to manually clean up:

```bash
# On the affected host(s):
iptables -F OUTPUT  # Flush OUTPUT chain
tc qdisc del dev eth0 root  # Remove tc rules
```

## Troubleshooting

### Test Times Out
- Increase the timeout in `wait_for_up_status()` calls
- Check if packet loss percentage is too high

### Migration Fails
- Check engine logs: `/var/log/ovirt-engine/engine.log`
- Check vdsm logs on hosts: `/var/log/vdsm/vdsm.log`
- Verify hosts can communicate via migration network

### Network Rules Not Applied
- Ensure hosts have root/sudo access via ansible
- Verify iptables and tc are installed on hosts
- Check ansible connection: `ansible hosts -m ping`

## Implementation Notes

1. **Engine IP Detection**: The test uses ansible facts to detect the engine's IP address dynamically
2. **Host Selection**: Tests determine source/destination hosts based on current VM placement
3. **Cleanup**: Network disruption rules are removed in `finally` blocks to ensure cleanup
4. **Logging**: Extensive logging helps debug network issues during test execution

## Future Enhancements

Potential additions to these tests:

- [ ] Test migration with complete network partition (100% packet loss)
- [ ] Test with asymmetric network issues (different issues on each host)
- [ ] Test migration rollback scenarios
- [ ] Test with storage network issues
- [ ] Measure migration time with/without network issues
- [ ] Test HA VM behavior during connectivity issues

## Related Tests

- `test_vm_operations.py::test_live_vm_migration_using_dedicated_network` - Basic migration test
- `test_required_network.py::test_required_network_host_non_operational` - Host non-operational scenarios
- `basic-suite-master/test-scenarios/test_006_migrations.py` - IPv4/IPv6 migration tests

## References

- [oVirt System Tests Documentation](../../docs/)
- [iptables Documentation](https://linux.die.net/man/8/iptables)
- [tc-netem Documentation](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
