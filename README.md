# vsphere-helper

Python scripts and docker image for managing vsphere.

- Export a VM/template from vsphere to OVF
- Deploy a OVF to vsphere

When running inside the docker, a S3 bucket can be used to transfer the files between vsphere and S3 directly.

## Running the scripts

```bash
pip install -r requirements.py
python exportovf.py -s <yourvsphere.com> -u <username> -p <password> -n <name-of-vm> -w <outputDir>
```

## Running with Docker

```bash
docker run -e USE_S3_BUCKET=<your-bucket-name> -e AWS_ACCESS_KEY_ID=<accessKeyId> -e AWS_SECRET_ACCESS_KEY=<accessKey> qinling/vsphere-helper exportovf.py -s <yourvsphere.com> -u <username> -p <password> -n <name-of-vm>
```
