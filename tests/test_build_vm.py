import base64
import importlib.util
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class BuildVmTests(unittest.TestCase):
    def test_vm_is_private_and_ssh_is_bastion_only(self):
        template = (ROOT / "infra/build-vm.bicep").read_text()
        self.assertNotIn("publicIPAddress", template)
        self.assertNotIn("adminPassword", template)
        self.assertNotIn("roleAssignments", template)
        self.assertIn("sourceAddressPrefix: bastionSubnetAddress", template)
        self.assertIn("destinationPortRange: '22'", template)
        self.assertIn("name: 'deny-other-inbound'", template)
        self.assertIn("disablePasswordAuthentication: true", template)
        self.assertIn("param vmSize string = 'Standard_B2s'", template)
        self.assertIn("diskSizeGB: 64", template)
        self.assertIn("storageAccountType: 'StandardSSD_LRS'", template)
        self.assertIn("offer: 'ubuntu-24_04-lts'", template)
        self.assertIn("secureBootEnabled: true, vTpmEnabled: true", template)

    def test_setup_failure_blocks_readiness(self):
        template = (ROOT / "infra/build-vm.bicep").read_text()
        self.assertIn("cloud-init status --wait && test -f /var/lib/aks-lab/build-ready", template)
        script = (ROOT / "infra/build-vm-init.sh").read_text()
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("set -euo pipefail", script)
        self.assertIn("sha256sum --check", script)
        self.assertIn('.host.security.rootless == true', script)
        self.assertIn('loginctl enable-linger "$username"', script)
        self.assertIn('systemctl start "user@$builder_uid.service"', script)
        self.assertIn('touch /var/lib/aks-lab/build-ready', script.splitlines()[-1])
        self.assertNotIn("az login", script)
        self.assertNotIn("apparmor_restrict_unprivileged_userns", script)

    def test_egress_order_and_region_ranges_are_wired(self):
        foundation = (ROOT / "infra/main.bicep").read_text()
        self.assertIn("spokeName: '${prefix}-vnet'", foundation)
        self.assertIn("deployPeering: false", foundation)
        self.assertIn("defaultOutboundAccess: false", foundation)
        self.assertIn("routeTable: { id: egress.outputs.routeTableId }", foundation)
        self.assertIn("managementAddress: '${networkPrefix}.16.0/24'", foundation)
        self.assertIn("hubName: egress.outputs.hubVnetName", foundation)
        self.assertIn("dependsOn: [peering]", foundation)
        guide = (ROOT / "labs/23-multi-region.md").read_text()
        self.assertIn("networkPrefix=10.60 hubPrefix=10.70", guide)
        self.assertIn('"buildVmSshPublicKey=$BuildSshPublicKey"', guide)
        firewall = (ROOT / "infra/firewall.bicep").read_text()
        management = firewall.split("name: 'build-management'", 1)[1].split("name: 'identity-gitops-observability'", 1)[0]
        self.assertIn("sourceAddresses: [managementAddress]", management)
        self.assertNotIn("sourceAddresses: [nodesAddress]", management)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML required for cloud-init validation")
    def test_cloud_init_packages_and_encoded_files(self):
        import yaml

        script = (ROOT / "infra/build-vm-init.sh").read_bytes()
        content = (ROOT / "infra/build-vm-cloud-init.yaml").read_text()
        content = content.replace("__BOOTSTRAP_SCRIPT__", base64.b64encode(script).decode())
        content = content.replace("__BUILD_CONFIG__", base64.b64encode(b'{"kubernetesVersion":"1.34.1","username":"labbuilder"}').decode())
        config = yaml.safe_load(content)
        self.assertTrue(config["package_update"])
        self.assertTrue({"podman", "uidmap", "slirp4netns", "fuse-overlayfs",
                         "python3-venv", "postgresql-client", "dnsutils", "dbus-user-session"}.issubset(config["packages"]))
        files = {entry["path"]: entry for entry in config["write_files"]}
        installer = files["/usr/local/sbin/setup-aks-build-vm"]
        self.assertEqual(installer["permissions"], "0755")
        self.assertEqual(base64.b64decode(installer["content"]), script)
        self.assertEqual(config["runcmd"], [["/usr/local/sbin/setup-aks-build-vm"]])

    def test_workflow_uses_supported_podman_metadata_capture(self):
        workflow = (ROOT / ".github/workflows/publish-image.yml").read_text()
        self.assertNotIn("--metadata-file", workflow)
        self.assertIn('podman image inspect "$image"', workflow)
        self.assertIn("--sbom=syft-spdx", workflow)
        self.assertIn("image-metadata.json", workflow)


if __name__ == "__main__":
    unittest.main()
