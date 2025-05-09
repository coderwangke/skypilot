# Smoke tests for SkyPilot for basic functionality
# Default options are set in pyproject.toml
# Example usage:
# Run all tests except for AWS and Lambda Cloud
# > pytest tests/smoke_tests/test_basic.py
#
# Terminate failed clusters after test finishes
# > pytest tests/smoke_tests/test_basic.py --terminate-on-failure
#
# Re-run last failed tests
# > pytest --lf
#
# Run one of the smoke tests
# > pytest tests/smoke_tests/test_basic.py::test_minimal
#
# Only run test for AWS + generic tests
# > pytest tests/smoke_tests/test_basic.py --aws
#
# Change cloud for generic tests to aws
# > pytest tests/smoke_tests/test_basic.py --generic-cloud aws

import os
import pathlib
import subprocess
import tempfile
import textwrap
import time

import pytest
from smoke_tests import smoke_tests_utils

import sky
from sky.skylet import constants
from sky.skylet import events
import sky.skypilot_config
from sky.utils import common_utils


# ---------- Dry run: 2 Tasks in a chain. ----------
@pytest.mark.no_vast  #requires GCP and AWS set up
@pytest.mark.no_fluidstack  #requires GCP and AWS set up
def test_example_app():
    test = smoke_tests_utils.Test(
        'example_app',
        ['python examples/example_app.py'],
    )
    smoke_tests_utils.run_one_test(test)


# ---------- A minimal task ----------
def test_minimal(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'minimal',
        [
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            # Output validation done.
            f'sky logs {name} 1 --status',
            f'sky logs {name} --status | grep "Job 1: SUCCEEDED"',  # Equivalent.
            # Test launch output again on existing cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky logs {name} --status | grep "Job 2: SUCCEEDED"',  # Equivalent.
            # Check the logs downloading
            f'log_path=$(sky logs {name} 1 --sync-down | grep "Job 1 logs:" | sed -E "s/^.*Job 1 logs: (.*)\\x1b\\[0m/\\1/g") && echo "$log_path" '
            # We need to explicitly expand the log path as it starts with ~, and linux does not automatically
            # expand it when having it in a variable.
            '  && expanded_log_path=$(eval echo "$log_path") && echo "$expanded_log_path" '
            '  && test -f $expanded_log_path/run.log',
            # Ensure the raylet process has the correct file descriptor limit.
            f'sky exec {name} "prlimit -n --pid=\$(pgrep -f \'raylet/raylet --raylet_socket_name\') | grep \'"\'1048576 1048576\'"\'"',
            f'sky logs {name} 3 --status',  # Ensure the job succeeded.
            # Install jq for the next test.
            f'sky exec {name} \'sudo apt-get update && sudo apt-get install -y jq\'',
            # Check the cluster info
            f'sky exec {name} \'echo "$SKYPILOT_CLUSTER_INFO" | jq .cluster_name | grep {name}\'',
            f'sky logs {name} 5 --status',  # Ensure the job succeeded.
            f'sky exec {name} \'echo "$SKYPILOT_CLUSTER_INFO" | jq .cloud | grep -i {generic_cloud}\'',
            f'sky logs {name} 6 --status',  # Ensure the job succeeded.
            # Test '-c' for exec
            f'sky exec -c {name} echo',
            f'sky logs {name} 7 --status',
            f'sky exec echo -c {name}',
            f'sky logs {name} 8 --status',
            f'sky exec -c {name} echo hi test',
            f'sky logs {name} 9 | grep "hi test"',
            f'sky exec {name} && exit 1 || true',
            f'sky exec -c {name} && exit 1 || true',
        ],
        f'sky down -y {name}',
        smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Test fast launch ----------
def test_launch_fast(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()

    test = smoke_tests_utils.Test(
        'test_launch_fast',
        [
            # First launch to create the cluster
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',

            # Second launch to test fast launch - should not reprovision
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --fast tests/test_yamls/minimal.yaml) && '
            ' echo "$s" && '
            # Validate that cluster was not re-launched.
            '! echo "$s" | grep -A 1 "Launching on" | grep "is up." && '
            # Validate that setup was not re-run.
            '! echo "$s" | grep -A 1 "Running setup on" | grep "running setup" && '
            # Validate that the task ran and finished.
            'echo "$s" | grep -A 1 "task run finish" | grep "Job finished (status: SUCCEEDED)"',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)


# See cloud exclusion explanations in test_autostop
@pytest.mark.no_fluidstack
@pytest.mark.no_lambda_cloud
@pytest.mark.no_ibm
@pytest.mark.no_kubernetes
@pytest.mark.no_nebius
def test_launch_fast_with_autostop(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    # Azure takes ~ 7m15s (435s) to autostop a VM, so here we use 600 to ensure
    # the VM is stopped.
    autostop_timeout = 600 if generic_cloud == 'azure' else 250
    test = smoke_tests_utils.Test(
        'test_launch_fast_with_autostop',
        [
            # First launch to create the cluster with a short autostop
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --cloud {generic_cloud} --fast -i 1 {smoke_tests_utils.LOW_RESOURCE_ARG} tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 1 --status',
            f'sky status -r {name} | grep UP',

            # Ensure cluster is stopped
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=autostop_timeout),
            # Even the cluster is stopped, cloud platform may take a while to
            # delete the VM.
            # FIXME(aylei): this can be flaky, sleep longer for now.
            f'sleep 60',
            # Launch again. Do full output validation - we expect the cluster to re-launch
            f'unset SKYPILOT_DEBUG; s=$(sky launch -y -c {name} --fast -i 1 tests/test_yamls/minimal.yaml) && {smoke_tests_utils.VALIDATE_LAUNCH_OUTPUT}',
            f'sky logs {name} 2 --status',
            f'sky status -r {name} | grep UP',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud) + autostop_timeout,
    )
    smoke_tests_utils.run_one_test(test)


# ------------ Test stale job ------------
@pytest.mark.no_fluidstack  # FluidStack does not support stopping instances in SkyPilot implementation
@pytest.mark.no_lambda_cloud  # Lambda Cloud does not support stopping instances
@pytest.mark.no_kubernetes  # Kubernetes does not support stopping instances
@pytest.mark.no_vast  # This requires port opening
def test_stale_job(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'stale_job',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            f'sky stop {name} -y',
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=100),
            f'sky start {name} -y',
            f'sky logs {name} 1 --status',
            f's=$(sky queue {name}); echo "$s"; echo; echo; echo "$s" | grep FAILED_DRIVER',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast
@pytest.mark.aws
def test_aws_stale_job_manual_restart():
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.AWS.max_cluster_name_length())
    region = 'us-east-2'
    test = smoke_tests_utils.Test(
        'aws_stale_job_manual_restart',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('aws', name),
            f'sky launch -y -c {name} --cloud aws --region {region} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(
                name,
                cmd=
                (f'id=`aws ec2 describe-instances --region {region} --filters '
                 f'Name=tag:ray-cluster-name,Values={name_on_cloud} '
                 f'--query Reservations[].Instances[].InstanceId '
                 f'--output text` && '
                 f'aws ec2 stop-instances --region {region} '
                 f'--instance-ids $id')),
            smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
                cluster_name=name,
                cluster_status=[sky.ClusterStatus.STOPPED],
                timeout=40),
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[sky.JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS),
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast
@pytest.mark.gcp
def test_gcp_stale_job_manual_restart():
    name = smoke_tests_utils.get_cluster_name()
    name_on_cloud = common_utils.make_cluster_name_on_cloud(
        name, sky.GCP.max_cluster_name_length())
    zone = 'us-central1-a'
    query_cmd = (f'gcloud compute instances list --filter='
                 f'"(labels.ray-cluster-name={name_on_cloud})" '
                 f'--zones={zone} --format="value(name)"')
    stop_cmd = (f'gcloud compute instances stop --zone={zone}'
                f' --quiet $({query_cmd})')
    test = smoke_tests_utils.Test(
        'gcp_stale_job_manual_restart',
        [
            smoke_tests_utils.launch_cluster_for_cloud_cmd('gcp', name),
            f'sky launch -y -c {name} --cloud gcp --zone {zone} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo hi"',
            f'sky exec {name} -d "echo start; sleep 10000"',
            # Stop the cluster manually.
            smoke_tests_utils.run_cloud_cmd_on_cluster(name, cmd=stop_cmd),
            'sleep 40',
            f'sky launch -c {name} -y "echo hi"',
            f'sky logs {name} 1 --status',
            f'sky logs {name} 3 --status',
            # Ensure the skylet updated the stale job status.
            smoke_tests_utils.
            get_cmd_wait_until_job_status_contains_without_matching_job(
                cluster_name=name,
                job_status=[sky.JobStatus.FAILED_DRIVER],
                timeout=events.JobSchedulerEvent.EVENT_INTERVAL_SECONDS)
        ],
        f'sky down -y {name} && {smoke_tests_utils.down_cluster_for_cloud_cmd(name)}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Check Sky's environment variables; workdir. ----------
@pytest.mark.no_fluidstack  # Requires amazon S3
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet
def test_env_check(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    total_timeout_minutes = 25 if generic_cloud == 'azure' else 15
    test = smoke_tests_utils.Test(
        'env_check',
        [
            f'sky launch -y -c {name} --cloud {generic_cloud} {smoke_tests_utils.LOW_RESOURCE_ARG} examples/env_check.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            # Test with only setup.
            f'sky launch -y -c {name} tests/test_yamls/test_only_setup.yaml',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 2 | grep "hello world"',
        ],
        f'sky down -y {name}',
        timeout=total_timeout_minutes * 60,
    )
    smoke_tests_utils.run_one_test(test)


# ---------- CLI logs ----------
@pytest.mark.no_scp  # SCP does not support num_nodes > 1 yet. Run test_scp_logs instead.
@pytest.mark.no_vast  # Vast does not support num_nodes > 1 yet.
def test_cli_logs(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    num_nodes = 2
    if generic_cloud == 'kubernetes':
        # Kubernetes does not support multi-node
        num_nodes = 1
    timestamp = time.time()
    test = smoke_tests_utils.Test('cli_logs', [
        f'sky launch -y -c {name} --cloud {generic_cloud} --num-nodes {num_nodes} {smoke_tests_utils.LOW_RESOURCE_ARG} "echo {timestamp} 1"',
        f'sky exec {name} "echo {timestamp} 2"',
        f'sky exec {name} "echo {timestamp} 3"',
        f'sky exec {name} "echo {timestamp} 4"',
        f'sky logs {name} 2 --status',
        f'sky logs {name} 3 4 --sync-down',
        f'sky logs {name} * --sync-down',
        f'sky logs {name} 1 | grep "{timestamp} 1"',
        f'sky logs {name} | grep "{timestamp} 4"',
    ], f'sky down -y {name}')
    smoke_tests_utils.run_one_test(test)


@pytest.mark.scp
def test_scp_logs():
    name = smoke_tests_utils.get_cluster_name()
    timestamp = time.time()
    test = smoke_tests_utils.Test(
        'SCP_cli_logs',
        [
            f'sky launch -y -c {name} {smoke_tests_utils.SCP_TYPE} "echo {timestamp} 1"',
            f'sky exec {name} "echo {timestamp} 2"',
            f'sky exec {name} "echo {timestamp} 3"',
            f'sky exec {name} "echo {timestamp} 4"',
            f'sky logs {name} 2 --status',
            f'sky logs {name} 3 4 --sync-down',
            f'sky logs {name} * --sync-down',
            f'sky logs {name} 1 | grep "{timestamp} 1"',
            f'sky logs {name} | grep "{timestamp} 4"',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ------- Testing the core API --------
# Most of the core APIs have been tested in the CLI tests.
# These tests are for testing the return value of the APIs not fully used in CLI.
def test_core_api_sky_launch_exec(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    cloud = sky.CLOUD_REGISTRY.from_str(generic_cloud)
    task = sky.Task(run="whoami")
    task.set_resources(
        sky.Resources(cloud=cloud, **smoke_tests_utils.LOW_RESOURCE_PARAM))
    try:
        job_id, handle = sky.get(sky.launch(task, cluster_name=name))
        assert job_id == 1
        assert handle is not None
        assert handle.cluster_name == name
        assert handle.launched_resources.cloud.is_same_cloud(cloud)
        job_id_exec, handle_exec = sky.get(sky.exec(task, cluster_name=name))
        assert job_id_exec == 2
        assert handle_exec is not None
        assert handle_exec.cluster_name == name
        assert handle_exec.launched_resources.cloud.is_same_cloud(cloud)
        # For dummy task (i.e. task.run is None), the job won't be submitted.
        dummy_task = sky.Task()
        job_id_dummy, _ = sky.get(sky.exec(dummy_task, cluster_name=name))
        assert job_id_dummy is None
    finally:
        sky.get(sky.down(name))


# The sky launch CLI has some additional checks to make sure the cluster is up/
# restarted. However, the core API doesn't have these; make sure it still works
@pytest.mark.no_kubernetes
@pytest.mark.no_nebius  # Nebius Autodown and Autostop not supported.
def test_core_api_sky_launch_fast(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    cloud = sky.CLOUD_REGISTRY.from_str(generic_cloud)
    try:
        task = sky.Task(run="whoami").set_resources(
            sky.Resources(cloud=cloud, **smoke_tests_utils.LOW_RESOURCE_PARAM))
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
        # Sleep to let the cluster autostop
        smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(
            cluster_name=name,
            cluster_status=[sky.ClusterStatus.STOPPED],
            timeout=120)
        # Run it again - should work with fast=True
        sky.launch(task,
                   cluster_name=name,
                   idle_minutes_to_autostop=1,
                   fast=True)
    finally:
        sky.down(name)


def test_jobs_launch_and_logs(generic_cloud: str):
    # Use the context manager
    with sky.skypilot_config.override_skypilot_config(
            smoke_tests_utils.LOW_CONTROLLER_RESOURCE_OVERRIDE_CONFIG):
        name = smoke_tests_utils.get_cluster_name()
        task = sky.Task(run="echo start job; sleep 30; echo end job")
        cloud = sky.CLOUD_REGISTRY.from_str(generic_cloud)
        task.set_resources(
            sky.Resources(cloud=cloud, **smoke_tests_utils.LOW_RESOURCE_PARAM))
        job_id, handle = sky.stream_and_get(sky.jobs.launch(task, name=name))
        assert handle is not None
        try:
            with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as f:
                sky.jobs.tail_logs(job_id=job_id, output_stream=f)
                f.seek(0)
                content = f.read()
                assert content.count('start job') == 1
                assert content.count('end job') == 1
        finally:
            sky.jobs.cancel(job_ids=[job_id])


# ---------- Testing YAML Specs ----------
# Our sky storage requires credentials to check the bucket existance when
# loading a task from the yaml file, so we cannot make it a unit test.
class TestYamlSpecs:
    # TODO(zhwu): Add test for `to_yaml_config` for the Storage object.
    #  We should not use `examples/storage_demo.yaml` here, since it requires
    #  users to ensure bucket names to not exist and/or be unique.
    _TEST_YAML_PATHS = [
        'examples/minimal.yaml', 'examples/managed_job.yaml',
        'examples/using_file_mounts.yaml', 'examples/resnet_app.yaml',
        'examples/multi_hostname.yaml'
    ]

    def _is_dict_subset(self, d1, d2):
        """Check if d1 is the subset of d2."""
        for k, v in d1.items():
            if k not in d2:
                if isinstance(v, list) or isinstance(v, dict):
                    assert len(v) == 0, (k, v)
                else:
                    assert False, (k, v)
            elif isinstance(v, dict):
                assert isinstance(d2[k], dict), (k, v, d2)
                self._is_dict_subset(v, d2[k])
            elif isinstance(v, str):
                if k == 'accelerators':
                    resources = sky.Resources()
                    resources._set_accelerators(v, None)
                    assert resources.accelerators == d2[k], (k, v, d2)
                else:
                    assert v.lower() == d2[k].lower(), (k, v, d2[k])
            else:
                assert v == d2[k], (k, v, d2[k])

    def _check_equivalent(self, yaml_path):
        """Check if the yaml is equivalent after load and dump again."""
        origin_task_config = common_utils.read_yaml(yaml_path)

        task = sky.Task.from_yaml(yaml_path)
        new_task_config = task.to_yaml_config()
        # d1 <= d2
        print(origin_task_config, new_task_config)
        self._is_dict_subset(origin_task_config, new_task_config)

    def test_load_dump_yaml_config_equivalent(self):
        """Test if the yaml config is equivalent after load and dump again."""
        pathlib.Path('~/datasets').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/tmpfile').expanduser().touch()
        pathlib.Path('~/.ssh').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/.ssh/id_rsa.pub').expanduser().touch()
        pathlib.Path('~/tmp-workdir').expanduser().mkdir(exist_ok=True)
        pathlib.Path('~/Downloads/tpu').expanduser().mkdir(parents=True,
                                                           exist_ok=True)
        for yaml_path in self._TEST_YAML_PATHS:
            self._check_equivalent(yaml_path)


# ---------- Testing Multiple Accelerators ----------
@pytest.mark.no_vast  # Vast has low availability for K80 GPUs
@pytest.mark.no_fluidstack  # Fluidstack does not support K80 gpus for now
@pytest.mark.no_paperspace  # Paperspace does not support K80 gpus
@pytest.mark.no_nebius  # Nebius does not support K80s
def test_multiple_accelerators_ordered():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
        timeout=20 * 60,
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
def test_multiple_accelerators_ordered_with_default():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-ordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_ordered_with_default.yaml | grep "Using user-specified accelerators list"',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep Spot',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
def test_multiple_accelerators_unordered():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-unordered',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Vast has low availability for T4 GPUs
@pytest.mark.no_fluidstack  # Fluidstack has low availability for T4 GPUs
@pytest.mark.no_paperspace  # Paperspace does not support T4 GPUs
@pytest.mark.no_nebius  # Nebius does not support T4 GPUs
def test_multiple_accelerators_unordered_with_default():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-accelerators-unordered-with-default',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_accelerators_unordered_with_default.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
            f'sky status {name} | grep Spot',
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.mark.no_vast  # Requires other clouds to be enabled
@pytest.mark.no_fluidstack  # Requires other clouds to be enabled
def test_multiple_resources():
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'multiple-resources',
        [
            f'sky launch -y -c {name} tests/test_yamls/test_multiple_resources.yaml',
            f'sky logs {name} 1 --status',  # Ensure the job succeeded.
        ],
        f'sky down -y {name}',
    )
    smoke_tests_utils.run_one_test(test)


# ---------- Sky Benchmark ----------
@pytest.mark.skip(reason='SkyBench is not supported in API server')
@pytest.mark.no_fluidstack  # Requires other clouds to be enabled
@pytest.mark.no_vast  # Requires other clouds to be enabled
@pytest.mark.no_paperspace  # Requires other clouds to be enabled
@pytest.mark.no_kubernetes
@pytest.mark.aws  # SkyBenchmark requires S3 access
def test_sky_bench(generic_cloud: str):
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'sky-bench',
        [
            f'sky bench launch -y -b {name} --cloud {generic_cloud} -i0 tests/test_yamls/minimal.yaml',
            'sleep 120',
            f'sky bench show {name} | grep sky-bench-{name} | grep FINISHED',
        ],
        f'sky bench down {name} -y; sky bench delete {name} -y',
    )
    smoke_tests_utils.run_one_test(test)


@pytest.fixture(scope='session')
def unreachable_context():
    """Setup the kubernetes context for the test.

    This fixture will copy the kubeconfig file and inject an unreachable context
    to it. So this must be session scoped that the kubeconfig is modified before
    the local API server starts.
    """
    # Get kubeconfig path from environment variable or use default
    kubeconfig_path = os.environ.get('KUBECONFIG',
                                     os.path.expanduser('~/.kube/config'))
    if not os.path.exists(kubeconfig_path):
        return
    import shutil

    # Create a temp kubeconfig
    temp_kubeconfig = tempfile.NamedTemporaryFile(delete=False, suffix='.yaml')
    shutil.copy(kubeconfig_path, temp_kubeconfig.name)
    original_kubeconfig = os.environ.get('KUBECONFIG')
    os.environ['KUBECONFIG'] = temp_kubeconfig.name

    free_port = common_utils.find_free_port(30000)
    unreachable_name = '_unreachable_context_'
    subprocess.run(
        'kubectl config set-cluster unreachable-cluster '
        f'--server=https://127.0.0.1:{free_port} && '
        'kubectl config set-credentials unreachable-user '
        '--token="aQo=" && '
        'kubectl config set-context ' + unreachable_name + ' '
        '--cluster=unreachable-cluster --user=unreachable-user && '
        # Restart the API server to pick up kubeconfig change
        # TODO(aylei): There is a implicit API server restart before starting
        # smoke tests in CI pipeline. We should move that to fixture to make
        # the test coherent.
        'sky api stop || true && sky api start',
        shell=True,
        check=True)

    yield unreachable_name

    # Clean up
    if original_kubeconfig:
        os.environ['KUBECONFIG'] = original_kubeconfig
    else:
        os.environ.pop('KUBECONFIG', None)
    os.unlink(temp_kubeconfig.name)


@pytest.mark.kubernetes
def test_kubernetes_context_failover(unreachable_context):
    """Test if the kubernetes context failover works.

    This test requires two kubernetes clusters:
    - kind-skypilot: the local cluster with mock labels for 8 H100 GPUs.
    - another accessible cluster: with enough CPUs
    To start the first cluster, run:
      sky local up
      # Add mock label for accelerator
      kubectl label node --overwrite skypilot-control-plane skypilot.co/accelerator=h100 --context kind-skypilot
      # Patch accelerator capacity
      kubectl patch node skypilot-control-plane --subresource=status -p '{"status": {"capacity": {"nvidia.com/gpu": "8"}}}' --context kind-skypilot
      # Add a new namespace to test the handling of namespaces
      kubectl create namespace test-namespace --context kind-skypilot
      # Set the namespace to test-namespace
      kubectl config set-context kind-skypilot --namespace=test-namespace --context kind-skypilot
    """
    # Get context that is not kind-skypilot
    contexts = subprocess.check_output('kubectl config get-contexts -o name',
                                       shell=True).decode('utf-8').split('\n')
    assert unreachable_context in contexts, (
        'unreachable_context should be initialized in the fixture')
    context = [
        context for context in contexts
        if (context != 'kind-skypilot' and context != unreachable_context)
    ][0]
    # Test unreachable context and non-existing context do not break failover
    config = textwrap.dedent(f"""\
    kubernetes:
      allowed_contexts:
        - {context}
        - {unreachable_context}
        - _nonexist_
        - kind-skypilot
    """)
    with tempfile.NamedTemporaryFile(delete=True) as f:
        f.write(config.encode('utf-8'))
        f.flush()
        name = smoke_tests_utils.get_cluster_name()
        test = smoke_tests_utils.Test(
            'kubernetes-context-failover',
            [
                # Check if kind-skypilot is provisioned with H100 annotations already
                'NODE_INFO=$(kubectl get nodes -o yaml --context kind-skypilot) && '
                'echo "$NODE_INFO" | grep nvidia.com/gpu | grep 8 && '
                'echo "$NODE_INFO" | grep skypilot.co/accelerator | grep h100 || '
                '{ echo "kind-skypilot does not exist '
                'or does not have mock labels for GPUs. Check the instructions in '
                'tests/test_smoke.py::test_kubernetes_context_failover." && exit 1; }',
                # Check namespace for kind-skypilot is test-namespace
                'kubectl get namespaces --context kind-skypilot | grep test-namespace || '
                '{ echo "Should set the namespace to test-namespace for kind-skypilot. Check the instructions in '
                'tests/test_smoke.py::test_kubernetes_context_failover." && exit 1; }',
                'sky show-gpus --cloud kubernetes --region kind-skypilot | grep H100 | grep "1, 2, 4, 8"',
                # Get contexts and set current context to the other cluster that is not kind-skypilot
                f'kubectl config use-context {context}',
                # H100 should not in the current context
                '! sky show-gpus --cloud kubernetes | grep H100',
                f'sky launch -y -c {name}-1 --cpus 1 echo hi',
                f'sky logs {name}-1 --status',
                # It should be launched not on kind-skypilot
                f'sky status -v {name}-1 | grep "{context}"',
                # Test failure for launching H100 on other cluster
                f'sky launch -y -c {name}-2 --gpus H100 --cpus 1 --cloud kubernetes --region {context} echo hi && exit 1 || true',
                # Test failover
                f'sky launch -y -c {name}-3 --gpus H100 --cpus 1 --cloud kubernetes echo hi',
                f'sky logs {name}-3 --status',
                # Test pods
                f'kubectl get pods --context kind-skypilot | grep "{name}-3"',
                # It should be launched on kind-skypilot
                f'sky status -v {name}-3 | grep "kind-skypilot"',
                # Should be 7 free GPUs
                f'sky show-gpus --cloud kubernetes --region kind-skypilot | grep H100 | grep "  7"',
                # Remove the line with "kind-skypilot"
                f'sed -i "/kind-skypilot/d" {f.name}',
                # Should still be able to exec and launch on existing cluster
                f'sky exec {name}-3 "echo hi"',
                f'sky logs {name}-3 --status',
                f'sky status -r {name}-3 | grep UP',
                f'sky launch -c {name}-3 --gpus h100 echo hi',
                f'sky logs {name}-3 --status',
                f'sky status -r {name}-3 | grep UP',
                # Test failure for launching on unreachable context
                f'kubectl config use-context {unreachable_context}',
                f'sky launch -y -c {name}-4 --gpus H100 --cpus 1 --cloud kubernetes --region {unreachable_context} echo hi && exit 1 || true',
                # Test failover from unreachable context
                f'sky launch -y -c {name}-5 --cpus 1 echo hi',
            ],
            f'sky down -y {name}-1 {name}-3 {name}-5',
            env={
                'SKYPILOT_CONFIG': f.name,
                constants.SKY_API_SERVER_URL_ENV_VAR:
                    sky.server.common.get_server_url()
            },
        )
        smoke_tests_utils.run_one_test(test)


def test_launch_and_exec_async(generic_cloud: str):
    """Test if the launch and exec commands work correctly with --async."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'launch_and_exec_async',
        [
            f'sky launch -c {name} -y --async',
            # Async exec.
            f'sky exec {name} echo --async',
            # Async exec and cancel immediately.
            (f's=$(sky exec {name} echo --async) && '
             'echo "$s" && '
             'cancel_cmd=$(echo "$s" | grep "To cancel the request" | '
             'sed -E "s/.*run: (sky api cancel .*).*/\\1/") && '
             'echo "Extracted cancel command: $cancel_cmd" && '
             '$cancel_cmd'),
            # Sync exec must succeed after command end.
            (
                f's=$(sky exec {name} echo) && echo "$s" && '
                'echo "===check exec output===" && '
                'job_id=$(echo "$s" | grep "Job submitted, ID:" | '
                'sed -E "s/.*Job submitted, ID: ([0-9]+).*/\\1/") && '
                f'sky logs {name} $job_id --status | grep "SUCCEEDED" && '
                # If job_id is 1, async_job_id will be 2, and vice versa.
                'async_job_id=$((3-job_id)) && '
                f'echo "===check async job===" && echo "Job ID: $async_job_id" && '
                # Wait async job to succeed.
                f'{smoke_tests_utils.get_cmd_wait_until_job_status_succeeded(name, "$async_job_id")}'
            ),
            # Cluster must be UP since the sync exec has been completed.
            f'sky status {name} | grep "UP"',
            # The cancelled job should not be scheduled, the job ID 3 is just
            # not exist.
            f'! sky logs {name} 3 --status | grep "SUCCEEDED"',
        ],
        teardown=f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud))
    smoke_tests_utils.run_one_test(test)


def test_cancel_launch_and_exec_async(generic_cloud: str):
    """Test if async launch and exec commands work correctly when cluster is shutdown"""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test('cancel_launch_and_exec_async', [
        (f'sky launch -c {name} -y --async && '
         f's=$(sky exec {name} echo --async) && '
         'echo "$s" && '
         'logs_cmd=$(echo "$s" | grep "Check logs with" | sed -E "s/.*with: (sky api logs .*).*/\\1/") && '
         'echo "Extracted logs command: $logs_cmd" && '
         f'{smoke_tests_utils.get_cmd_wait_until_cluster_status_contains(name, [sky.ClusterStatus.INIT], 30)} &&'
         f'sky down -y {name} && '
         'log_output=$(eval $logs_cmd || true) && '
         'echo "===logs===" && echo "$log_output" && '
         'echo "$log_output" | grep "cancelled"'),
    ],
                                  teardown=f'sky down -y {name}',
                                  timeout=smoke_tests_utils.get_timeout(
                                      generic_cloud))
    smoke_tests_utils.run_one_test(test)


# ---------- Testing Exit Codes for CLI commands ----------
def test_cli_exit_codes(generic_cloud: str):
    """Test that CLI commands properly return exit codes based on job success/failure."""
    name = smoke_tests_utils.get_cluster_name()
    test = smoke_tests_utils.Test(
        'cli_exit_codes',
        [
            # Test successful job exit code (0)
            f'sky launch -y -c {name} --cloud {generic_cloud} "echo success" && echo "Exit code: $?"',
            f'sky logs {name} 1 --status | grep SUCCEEDED',

            # Test that sky logs with successful job returns 0
            f'sky logs {name} 1 && echo "Exit code: $?"',

            # Test failed job exit code (100)
            f'sky exec {name} "exit 1" || echo "Command failed with code: $?" | grep "Command failed with code: 100"',
            f'sky logs {name} 2 --status | grep FAILED',
            f'sky logs {name} 2 || echo "Job logs exit code: $?" | grep "Job logs exit code: 100"',
        ],
        f'sky down -y {name}',
        timeout=smoke_tests_utils.get_timeout(generic_cloud),
    )
    smoke_tests_utils.run_one_test(test)
