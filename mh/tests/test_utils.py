"""Tests for the utility functions in mindforge_harness.utils."""
from mindforge_harness.utils import extract_missing_tests, extract_modified_test_files


def test_extract_missing_tests():
    """Test the extraction of missing tests from the stderr output."""
    log = """
    ERROR: not found: /workspace/tests/models/test_url.py::test_param_with_space
    (no name '/workspace/tests/models/test_url.py::test_param_with_space' in any of [<Module test_url.py>])

    ERROR: not found: /workspace/tests/models/test_url.py::test_query_with_mixed_percent_encoding
    (no name '/workspace/tests/models/test_url.py::test_query_with_mixed_percent_encoding' in any of [<Module test_url.py>])

    ERROR: not found: /workspace/tests/models/test_url.py::test_query_requiring_percent_encoding
    (no name '/workspace/tests/models/test_url.py::test_query_requiring_percent_encoding' in any of [<Module test_url.py>])

    ERROR: not found: /workspace/tests/models/test_url.py::test_query_with_existing_percent_encoding
    (no name '/workspace/tests/models/test_url.py::test_query_with_existing_percent_encoding' in any of [<Module test_url.py>])
    """

    expected = [
        "tests/models/test_url.py::test_param_with_space",
        "tests/models/test_url.py::test_query_with_mixed_percent_encoding",
        "tests/models/test_url.py::test_query_requiring_percent_encoding",
        "tests/models/test_url.py::test_query_with_existing_percent_encoding",
    ]

    assert extract_missing_tests(log) == expected

def test_extract_modified_test_files():
    """Test the extraction of modified test files from the stdout output."""
    log = """diff --git a/scripts/dependency_test.sh b/scripts/dependency_test.sh
--- a/scripts/dependency_test.sh
+++ b/scripts/dependency_test.sh
@@ -33,7 +33,7 @@ valid_service() {
# Verify whether this is a valid service
# We'll ignore metadata folders, and folders that test generic Moto behaviour
# We'll also ignore CloudFormation, as it will always depend on other services
-  local ignore_moto_folders="core instance_metadata __pycache__ templates cloudformation moto_api moto_server resourcegroupstaggingapi packages utilities s3bucket_path"
+  local ignore_moto_folders="core instance_metadata __pycache__ templates cloudformation moto_api moto_server packages utilities s3bucket_path"
if echo $ignore_moto_folders | grep -q "$1"; then
    return 1
else
diff --git a/tests/test_resourcegroupstaggingapi/test_resourcegroupstagging_glue.py b/tests/test_resourcegroupstaggingapi/test_resourcegroupstagging_glue.py
new file mode 100644
--- /dev/null
+++ b/tests/test_resourcegroupstaggingapi/test_resourcegroupstagging_glue.py
@@ -0,0 +1,55 @@
+import boto3
+
+from moto import mock_glue, mock_resourcegroupstaggingapi
+from moto.core import DEFAULT_ACCOUNT_ID
+from uuid import uuid4
+
+
+@mock_glue
+@mock_resourcegroupstaggingapi
+def test_glue_jobs():
+    glue = boto3.client("glue", region_name="us-west-1")
+    job_name = glue.create_job(
+        Name=str(uuid4()),
+        Role="test_role",
+        Command=dict(Name="test_command"),
+        Tags={"k1": "v1"},
+    )["Name"]
+    job_arn = f"arn:aws:glue:us-west-1:{DEFAULT_ACCOUNT_ID}:job/{job_name}"
+
+    rtapi = boto3.client("resourcegroupstaggingapi", region_name="us-west-1")
+    resources = rtapi.get_resources(ResourceTypeFilters=["glue"])[
+        "ResourceTagMappingList"
+    ]
+    assert resources == [
+        {"ResourceARN": job_arn, "Tags": [{"Key": "k1", "Value": "v1"}]}
+    ]
+
+    resources = rtapi.get_resources(ResourceTypeFilters=["glue:job"])[
+        "ResourceTagMappingList"
+    ]
+    assert resources == [
+        {"ResourceARN": job_arn, "Tags": [{"Key": "k1", "Value": "v1"}]}
+    ]
+
+    resources = rtapi.get_resources(TagFilters=[{"Key": "k1", "Values": ["v1"]}])[
+        "ResourceTagMappingList"
+    ]
+    assert resources == [
+        {"ResourceARN": job_arn, "Tags": [{"Key": "k1", "Value": "v1"}]}
+    ]
+
+    resources = rtapi.get_resources(ResourceTypeFilters=["glue:table"])[
+        "ResourceTagMappingList"
+    ]
+    assert resources == []
+
+    resources = rtapi.get_resources(ResourceTypeFilters=["ec2"])[
+        "ResourceTagMappingList"
+    ]
+    assert resources == []
+
+    assert rtapi.get_tag_keys()["TagKeys"] == ["k1"]
+
+    assert rtapi.get_tag_values(Key="k1")["TagValues"] == ["v1"]
+    assert rtapi.get_tag_values(Key="unknown")["TagValues"] == []
"""
    result = extract_modified_test_files(log)
    assert set(result) == set(
        [
            "tests/test_resourcegroupstaggingapi/test_resourcegroupstagging_glue.py"
        ]
    )