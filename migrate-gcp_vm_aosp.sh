#!/bin/bash
# ================================================
# AOSP BUILDER MIGRATION SCRIPT
# Migrated from us-central1-a to us-east1-b
# Project: project-60b488c4-6afd-48d6-913
# ================================================

set -e  # Exit on error

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
  --enable-nested-virtualization \
  --maintenance-policy=MIGRATE \
  --scopes=cloud-platform,storage-rw

echo "✅ New VM created successfully in $NEW_ZONE"

# ====================== 4. SHOW STATUS ======================
echo "4. Current VM Status:"
gcloud compute instances describe $VM_NAME \
  --zone=$NEW_ZONE \
  --format="table(name,zone,status,machineType,networkInterfaces[0].accessConfigs[0].natIP)"

echo ""
echo "=== How to access your VM ==="
echo "gcloud compute ssh $VM_NAME --zone=$NEW_ZONE"
echo ""
echo "Your AOSP source tree should be fully available inside the VM."

# ====================== 5. CLEANUP (Uncomment when ready) ======================
# echo "5. Cleanup (old VM + snapshot)..."
# gcloud compute instances delete $VM_NAME --zone=$OLD_ZONE --quiet
# gcloud compute snapshots delete $SNAPSHOT_NAME --quiet
# echo "✅ Cleanup completed."

# gcloud compute instances start aosp-builder-cutterfish --zone=us-east1-b

NEW_ZONE="us-east1-b"
# 1. Stop the instance
gcloud compute instances stop aosp-builder-cutterfish --zone=$NEW_ZONE
# 2. Enable nested virtualization
gcloud compute instances update aosp-builder-cutterfish \
  --enable-nested-virtualization \
  --zone=$NEW_ZONE \
  --min-cpu-platform="Intel Haswell"
# 3. Start the instance again
gcloud compute instances start aosp-builder-cutterfish --zone=$NEW_ZONE  

# Example zone (common ones): us-central1-a, us-east1-b, asia-southeast1-a, etc.
# Check your zone: gcloud compute instances describe aosp-builder-cutterfish --format='get(zone)'

echo "=== Script finished ==="