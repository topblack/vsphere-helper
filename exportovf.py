#!/usr/bin/env python
"""
Written by Leon Qin, based on VMWare's official sample code
Export a VM/template to OVF to a local work directory
"""

import argparse
import atexit
import sys
import os
import threading
from time import sleep
import requests
from pyVim.connect import SmartConnectNoSSL, Disconnect
from pyVmomi import vim, vmodl

# disable  urllib3 warnings
if hasattr(requests.packages.urllib3, 'disable_warnings'):
    requests.packages.urllib3.disable_warnings()


def get_args():
    """Get command line args from the user.
    """
    parser = argparse.ArgumentParser(
        description='arguments for interacting with vsphere')

    parser.add_argument('-s', '--host',
                        required=True,
                        action='store',
                        help='vSphere service to connect to')
    parser.add_argument('-o', '--port',
                        type=int,
                        default=443,
                        action='store',
                        help='Port to connect on.')
    parser.add_argument('-u', '--user',
                        required=True,
                        action='store',
                        help='User name to use when connecting to host')
    parser.add_argument('-p', '--password',
                        required=False,
                        action='store',
                        help='Password to use when connecting to host')
    parser.add_argument('-n', '--name',
                        required=True,
                        action='store',
                        help='The name of the VM to export.')
    parser.add_argument('-w', '--workdir',
                        required=True,
                        action='store',
                        help='Working directory. Must have write permission.')
    args = parser.parse_args()

    if not args.password:
        args.password = getpass(prompt='Enter password: ')

    return args


def get_vmuuid_by_name(si, name):
    content = si.RetrieveContent()

    container = content.rootFolder  # starting point to look into
    viewType = [vim.VirtualMachine]  # object types to look for
    recursive = True  # whether we should look into it recursively
    containerView = content.viewManager.CreateContainerView(
        container, viewType, recursive)

    children = containerView.view

    for child in children:
        if name == child.summary.config.name:
            return child.summary.config.instanceUuid

    return ""


def print_http_nfc_lease_info(info):
    """ Prints information about the lease,
    such as the entity covered by the lease,
    and HTTP URLs for up/downloading file backings.
    :param info:
    :type info: vim.HttpNfcLease.Info
    :return:
    """
    print('Lease timeout: {0.leaseTimeout}\n'
          'Disk Capacity KB: {0.totalDiskCapacityInKB}'.format(info))
    device_number = 1
    if info.deviceUrl:
        for device_url in info.deviceUrl:
            print('HttpNfcLeaseDeviceUrl: {1}\n'
                  'Device URL Import Key: {0.importKey}\n'
                  'Device URL Key: {0.key}\n'
                  'Device URL: {0.url}\n'
                  'Device URL Size: {0.fileSize}\n'
                  'SSL Thumbprint: {0.sslThumbprint}\n'.format(device_url,
                                                               device_number))
            device_number += 1
    else:
        print('No devices were found.')


def break_down_cookie(cookie):
    """ Breaks down vSphere SOAP cookie
    :param cookie: vSphere SOAP cookie
    :type cookie: str
    :return: Dictionary with cookie_name: cookie_value
    """
    cookie_a = cookie.split(';')
    cookie_name = cookie_a[0].split('=')[0]
    cookie_text = ' {0}; ${1}'.format(cookie_a[0].split('=')[1],
                                      cookie_a[1].lstrip())
    return {cookie_name: cookie_text}


class LeaseProgressUpdater(threading.Thread):
    """
        Lease Progress Updater & keep alive
        thread
    """

    def __init__(self, http_nfc_lease, update_interval):
        threading.Thread.__init__(self)
        self._running = True
        self.httpNfcLease = http_nfc_lease
        self.updateInterval = update_interval
        self.progressPercent = 0

    def set_progress_pct(self, progress_pct):
        self.progressPercent = progress_pct

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                if self.httpNfcLease.state == vim.HttpNfcLease.State.done:
                    return
                print('Updating HTTP NFC Lease '
                      'Progress to {}%'.format(self.progressPercent))
                self.httpNfcLease.HttpNfcLeaseProgress(self.progressPercent)
                sleep(self.updateInterval)
            except ex:
                print(ex.message)
                return


def download_device(headers, cookies, temp_target_disk,
                    device_url, lease_updater,
                    total_bytes_written, total_bytes_to_write):
    """ Download disk device of HttpNfcLease.info.deviceUrl
    list of devices
    :param headers: Request headers
    :type cookies: dict
    :param cookies: Request cookies (session)
    :type cookies: dict
    :param temp_target_disk: file name to write
    :type temp_target_disk: str
    :param device_url: deviceUrl.url
    :type device_url: str
    :param lease_updater:
    :type lease_updater: LeaseProgressUpdater
    :param total_bytes_written: Bytes written so far
    :type total_bytes_to_write: long
    :param total_bytes_to_write: VM unshared storage
    :type total_bytes_to_write: long
    :return:
    """
    with open(temp_target_disk, 'wb') as handle:
        response = requests.get(device_url, stream=True,
                                headers=headers,
                                cookies=cookies, verify=False)
        # response other than 200
        if not response.ok:
            response.raise_for_status()
        # keeping track of progress
        current_bytes_written = 0
        for block in response.iter_content(chunk_size=2*1024*1024):
            # filter out keep-alive new chunks
            if block:
                handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
            # getting right progress
            current_bytes_written += len(block)
            written_pct = ((current_bytes_written +
                            total_bytes_written) * 100) / total_bytes_to_write
            # updating lease
            lease_updater.progressPercent = int(written_pct)
    return current_bytes_written


def export_vm(si, uuid, workdir):

    # Getting VM data
    vm_obj = si.content.searchIndex.FindByUuid(None, uuid, True, True)
    # VM does exist
    if not vm_obj:
        raise Exception('VM {} does not exist'.format(uuid))

    # VM must be powered off to export
    if not vm_obj.runtime.powerState == \
            vim.VirtualMachine.PowerState.poweredOff:
        raise Exception('VM {} must be powered off'.format(vm_obj.name))

    # Breaking down SOAP Cookie &
    # creating Header
    soap_cookie = si._stub.cookie
    cookies = break_down_cookie(soap_cookie)
    headers = {'Accept': 'application/x-vnd.vmware-streamVmdk'}  # not required

    # checking if working directory exists
    print('Working dir: {} '.format(workdir))
    if not os.path.isdir(workdir):
        print('Creating working directory {}'.format(workdir))
        os.mkdir(workdir)
    # actual target directory for VM
    target_directory = os.path.join(workdir, vm_obj.name)
    print('Target dir: {}'.format(target_directory))
    if not os.path.isdir(target_directory):
        print('Creating target dir {}'.format(target_directory))
        os.mkdir(target_directory)

    # Getting HTTP NFC Lease
    http_nfc_lease = vm_obj.ExportVm()

    # starting lease updater
    lease_updater = LeaseProgressUpdater(http_nfc_lease, 60)
    lease_updater.start()

    # Creating list for ovf files which will be value of
    # ovfFiles parameter in vim.OvfManager.CreateDescriptorParams
    ovf_files = list()
    total_bytes_written = 0
    # http_nfc_lease.info.totalDiskCapacityInKB not real
    # download size
    total_bytes_to_write = vm_obj.summary.storage.unshared
    try:
        while True:
            if http_nfc_lease.state == vim.HttpNfcLease.State.ready:
                print('HTTP NFC Lease Ready')
                print_http_nfc_lease_info(http_nfc_lease.info)

                for deviceUrl in http_nfc_lease.info.deviceUrl:
                    if not deviceUrl.targetId:
                        print("No targetId found for url: {}."
                              .format(deviceUrl.url))
                        print("Device is not eligible for export. This "
                              "could be a mounted iso or img of some sort")
                        print("Skipping...")
                        continue

                    temp_target_disk = os.path.join(target_directory,
                                                    deviceUrl.targetId)
                    print('Downloading {} to {}'.format(deviceUrl.url,
                                                        temp_target_disk))
                    current_bytes_written = download_device(
                        headers=headers, cookies=cookies,
                        temp_target_disk=temp_target_disk,
                        device_url=deviceUrl.url,
                        lease_updater=lease_updater,
                        total_bytes_written=total_bytes_written,
                        total_bytes_to_write=total_bytes_to_write)
                    # Adding up file written bytes to total
                    total_bytes_written += current_bytes_written
                    print('Creating OVF file for {}'.format(temp_target_disk))
                    # Adding Disk to OVF Files list
                    ovf_file = vim.OvfManager.OvfFile()
                    ovf_file.deviceId = deviceUrl.key
                    ovf_file.path = deviceUrl.targetId
                    ovf_file.size = current_bytes_written
                    ovf_files.append(ovf_file)
                break
            elif http_nfc_lease.state == vim.HttpNfcLease.State.initializing:
                print('HTTP NFC Lease Initializing.')
            elif http_nfc_lease.state == vim.HttpNfcLease.State.error:
                raise Exception("HTTP NFC Lease error: {}".format(
                    http_nfc_lease.state.error))
            sleep(2)
        print('Getting OVF Manager')
        ovf_manager = si.content.ovfManager
        print('Creating OVF Descriptor')
        vm_descriptor_name = vm_obj.name
        ovf_parameters = vim.OvfManager.CreateDescriptorParams()
        ovf_parameters.name = vm_descriptor_name
        ovf_parameters.ovfFiles = ovf_files
        vm_descriptor_result = ovf_manager.CreateDescriptor(obj=vm_obj,
                                                            cdp=ovf_parameters)
        if vm_descriptor_result.error:
            raise vm_descriptor_result.error[0].fault
        else:
            vm_descriptor = vm_descriptor_result.ovfDescriptor
            target_ovf_descriptor_path = os.path.join(target_directory,
                                                      vm_descriptor_name +
                                                      '.ovf')
            print('Writing OVF Descriptor {}'.format(
                target_ovf_descriptor_path))
            with open(target_ovf_descriptor_path, 'wb') as handle:
                handle.write(vm_descriptor.encode())
            # ending lease
            http_nfc_lease.HttpNfcLeaseProgress(100)
            http_nfc_lease.HttpNfcLeaseComplete()
            # stopping thread
            lease_updater.stop()
    except ex:
        # Complete lease upon exception
        http_nfc_lease.HttpNfcLeaseComplete()
        raise ex


def main():
    args = get_args()
    print("vsphere: {serviceHost}, VM: {vmName}".format(
        serviceHost=args.host, vmName=args.name))

    try:
        si = SmartConnectNoSSL(host=args.host,
                               user=args.user,
                               pwd=args.password,
                               port=args.port)

        atexit.register(Disconnect, si)

        vmid = get_vmuuid_by_name(si, args.name)

        if not vmid:
            print("VM {name} not found".format(name=args.name))
            return -1

        export_vm(si, vmid, args.workdir)

    except Exception as ex:
        print(ex)
        return -2


if __name__ == '__main__':
    sys.exit(main())
