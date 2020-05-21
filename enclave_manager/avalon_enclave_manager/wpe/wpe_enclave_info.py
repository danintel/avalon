# Copyright 2020 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import time
import random
import logging

from ssl import SSLError
from requests.exceptions import Timeout
from requests.exceptions import HTTPError
from abc import ABC, abstractmethod
import avalon_crypto_utils.keys as keys
import avalon_enclave_manager.wpe.wpe_enclave as enclave
from avalon_enclave_manager.base_enclave_info import BaseEnclaveInfo

logger = logging.getLogger(__name__)


class WorkOrderProcessorEnclaveInfo(BaseEnclaveInfo):
    """
    WPE Info class to initialize enclave, signup enclave and hold
    data obtained post signup.
    """

    # -------------------------------------------------------
    def __init__(self, config):

        # Initialize the keys that can be used later to
        # register the enclave
        enclave._SetLogger(logger)
        super().__init__(enclave.is_sgx_simulator())
        self._initialize_enclave(config)

    # -------------------------------------------------------

    def create_enclave_signup_data(self, ex_data):
        """
        Create enclave signup data

        Parameters :
            @param ex_data - Ex_data containing the public verification key
                             generated by KME
        Returns :
            @returns enclave_info - A dictionary of enclave data
        """

        ias_nonce = '{0:032X}'.format(random.getrandbits(128))
        try:
            enclave_data = self._create_signup_info(ias_nonce, ex_data)
        except Exception as err:
            raise Exception('failed to create enclave signup data; {}'
                            .format(str(err)))
        try:
            self.ias_nonce = ias_nonce
            self.verifying_key = enclave_data.verifying_key
            self.encryption_key = enclave_data.encryption_key
            self.encryption_key_signature = \
                enclave_data.encryption_key_signature
            self.enclave_id = enclave_data.verifying_key
            self.proof_data = ''
            if not enclave.is_sgx_simulator():
                self.proof_data = enclave_data.proof_data
            self.enclave_keys = \
                keys.EnclaveKeys(self.verifying_key, self.encryption_key)
            # No sealed data is present for WPE
            self.sealed_data = ""
        except AttributeError as attr:
            raise Exception("missing enclave initialization parameter; {}"
                            .format(str(attr)))

    # -----------------------------------------------------------------

    def _create_signup_info(self, ias_nonce, ex_data):
        """
        Create enclave signup data

        Parameters :
            @param ias_nonce - Used in IAS request to verify attestation
                               as a distinguishing factor
            @param ex_data - Ex_data containing the public verification key
                             generated by KME
        Returns :
            @returns signup_info_obj - Signup info data
        """

        # Part of what is returned with the signup data is an enclave quote, we
        # want to update the revocation list first.
        self._update_sig_rl()
        # Now, let the enclave create the signup data

        signup_cpp_obj = enclave.SignupInfoWPE()
        # @TODO : Passing in_ext_data_signature & in_kme_attestation as
        # empty string "" as of now
        signup_data = signup_cpp_obj.CreateEnclaveData(ex_data, "", "")
        if signup_data is None:
            return None

        signup_info = self._get_signup_info(
            signup_data, signup_cpp_obj, ias_nonce)
        # Now we can finally serialize the signup info and create a
        # corresponding signup info object.
        signup_info_obj = signup_cpp_obj.DeserializeSignupInfo(
            json.dumps(signup_info))
        # Now we can return the real object
        return signup_info_obj

    # -----------------------------------------------------------------

    def get_enclave_public_info(self):
        """
        Return information about the enclave

        Returns :
            @returns A dict of sealed data
        """
        signup_cpp_obj = enclave.SignupInfoWPE()
        return signup_cpp_obj.UnsealEnclaveData()

    # -----------------------------------------------------------------

    def _verify_enclave_info(self, enclave_info, mr_enclave, signup_cpp_obj):
        """
        Verifies enclave signup info

        Parameters :
            @param enclave_info - A JSON serialised enclave signup info
                                  along with IAS attestation report
            @param mr_enclave - enclave measurement value
            @param signup_cpp_obj - CPP object to access signup APIs
        Returns :
            @returns 0 - If verification passed
                     1 - Otherwise
        """
        return signup_cpp_obj.VerifyEnclaveInfoWPE(enclave_info, mr_enclave)

    # ----------------------------------------------------------------

    def _init_enclave_with(self, signed_enclave, config):
        """
        Initialize and return tcf_enclave_info that holds details about
        the WPE

        Parameters :
            @param signed_enclave - The enclave binary read from filesystem
            @param config - A dictionary of configurations
        Returns :
            @returns tcf_enclave_info - An instance of the tcf_enclave_info
        """
        return enclave.tcf_enclave_info(
            signed_enclave, config['spid'], int(config['num_of_enclaves']))

    # -----------------------------------------------------------------