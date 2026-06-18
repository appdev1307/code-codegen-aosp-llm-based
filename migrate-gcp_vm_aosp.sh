#!/bin/bash
# ================================================
# AOSP BUILDER MIGRATION SCRIPT
# Migrated from us-central1-a to us-east1-b
# Project: project-60b488c4-6afd-48d6-913
# ================================================
set -e # Exit on error

echo "=== AOSP Builder Migration Script ==="

# ====================== VARIABLES ======================
VM_NAME="aosp-builder-cutterfish"
OLD_ZONE="us-central1-a"
NEW_ZONE="us-east1-b"
SNAPSHOT_NAME="aosp-builder-cutterfish-20260617-1135"
MACHINE_TYPE="n2-standard-16"
DISK_SIZE="500"
DISK_TYPE="pd-standard"

# ====================== 1. CHECK OLD VM ======================
echo "1. Checking old VM status..."
gcloud compute instances describe $VM_NAME --zone=$OLD_ZONE --format="value(status)" || echo "Old VM not found."

# ====================== 2. CREATE SNAPSHOT (if not exists) ======================
echo "2. Creating snapshot (if needed)..."
if ! gcloud compute snapshots describe $SNAPSHOT_NAME >/dev/null 2>&1; then
  BOOT_DISK=$(gcloud compute instances describe $VM_NAME \
    --zone=$OLD_ZONE --format="value(disks[0].source.basename())")
 
  echo "Creating snapshot from disk: $BOOT_DISK"
  gcloud compute disks snapshot $BOOT_DISK \
    --zone=$OLD_ZONE \
    --snapshot-names=$SNAPSHOT_NAME
  echo "✅ Snapshot created: $SNAPSHOT_NAME"
else
  echo "✅ Snapshot $SNAPSHOT_NAME already exists."
fi

# ====================== 3. CREATE NEW VM ======================
echo "3. Creating new VM in $NEW_ZONE..."
gcloud compute instances create $VM_NAME \
  --zone=$NEW_ZONE \
  --source-snapshot=$SNAPSHOT_NAME \
  --machine-type=$MACHINE_TYPE \
  --boot-disk-size=$DISK_SIZE \
  --boot-disk-type=$DISK_TYPE \
  --network-interface=network=default,network-tier=PREMIUM \
  --maintenance-policy=MIGRATE \
  --scopes=cloud-platform,storage-rw

echo "✅ New VM created successfully in $NEW_ZONE"

# ====================== 4. SHOW STATUS ======================
echo "4. Current VM Status:"
gcloud compute instances describe $VM_NAME \
  --zone=$NEW_ZONE \
  --format="table(name,zone,status,machineType,networkInterfaces[0].accessConfigs[0].natIP)"

# ====================== 5. ENABLE NESTED VIRTUALIZATION (Critical for Cuttlefish) ======================
echo ""
echo "5. Enabling Nested Virtualization..."

gcloud compute instances start $VM_NAME --zone=$NEW_ZONE

gcloud compute ssh $VM_NAME --zone=$NEW_ZONE

# Stop the instance
echo "   Stopping instance..."
gcloud compute instances stop $VM_NAME --zone=$NEW_ZONE

# Export current config
echo "   Exporting instance config..."
gcloud compute instances export $VM_NAME --zone=$NEW_ZONE > nested_config.yaml

# Add nested virtualization
cat >> nested_config.yaml << EOF

advancedMachineFeatures:
  enableNestedVirtualization: true
EOF

echo "   Applying nested virtualization config..."
gcloud compute instances update-from-file $VM_NAME \
  --source=nested_config.yaml \
  --most-disruptive-allowed-action=RESTART \
  --zone=$NEW_ZONE

# Start the instance
echo "   Starting instance..."
gcloud compute instances start $VM_NAME --zone=$NEW_ZONE

echo "✅ Nested virtualization enabled successfully!"

# ====================== 6. FINAL STATUS ======================
echo ""
echo "6. Final VM Status:"
gcloud compute instances describe $VM_NAME \
  --zone=$NEW_ZONE \
  --format="table(name,zone,status,machineType,networkInterfaces[0].accessConfigs[0].natIP)"

echo ""
echo "=== How to access your VM ==="
echo "gcloud compute ssh $VM_NAME --zone=$NEW_ZONE"
echo ""
echo "After SSH, run these to verify KVM:"
echo "   ls -l /dev/kvm"
echo "   grep -c -w 'vmx\|svm' /proc/cpuinfo"
echo ""
echo "Then launch Cuttlefish with:"
echo "   launch_cvd --noresume --cpus=8 --memory_mb=8192 --gpu_mode=guest_swiftshader"
echo ""
echo "=== Script finished ==="