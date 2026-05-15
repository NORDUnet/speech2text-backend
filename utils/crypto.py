# Copyright (c) 2025-2026 Sunet.
# Contributor: Kristofer Hallin
#
# This file is part of Sunet Scribe.
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

import io
import os
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import BinaryIO, Iterator, Optional, Tuple

FILE_MAGIC_V2 = b"S2TE2"


def generate_rsa_keypair(
    key_size: int = 4096,
) -> Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """
    Generate an RSA key pair.
    Returns a tuple of (private_key, public_key).

    Parameters:
        key_size (int): Size of the RSA key in bits. Default is 4096.

    Returns:
        Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]: The generated RSA private and public keys.
    """

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )
    return private_key, private_key.public_key()


def serialize_private_key_to_pem(
    private_key: rsa.RSAPrivateKey,
    password: bytes,
) -> bytes:
    """
    Serialize the private key to PEM format.
    If a password is provided, the key will be encrypted.

    Parameters:
        private_key (rsa.RSAPrivateKey): The RSA private key to serialize.
        password (bytes): The password to encrypt the private key.

    Returns:
        bytes: The PEM-formatted private key.
    """

    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )


def serialize_public_key_to_pem(
    public_key: rsa.RSAPublicKey,
) -> bytes:
    """
    Serialize the public key to PEM format.

    Parameters:
        public_key (rsa.RSAPublicKey): The RSA public key to serialize.

    Returns:
        bytes: The PEM-formatted public key.
    """

    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def deserialize_private_key_from_pem(
    pem_data: bytes,
    password: bytes,
) -> rsa.RSAPrivateKey:
    """
    Deserialize a PEM-formatted private key.
    If the key is encrypted, provide the password.

    Parameters:
        pem_data (bytes): The PEM-formatted private key data.
        password (bytes): The password to decrypt the private key.

    Returns:
        rsa.RSAPrivateKey: The deserialized RSA private key.
    """

    if not isinstance(password, bytes):
        password = password.encode("utf-8")
    if not isinstance(pem_data, bytes):
        pem_data = pem_data.encode("utf-8")

    return serialization.load_pem_private_key(pem_data, password=password)


def deserialize_public_key_from_pem(
    pem_data: bytes,
) -> rsa.RSAPublicKey:
    """
    Deserialize a PEM-formatted public key.

    Parameters:
        pem_data (bytes): The PEM-formatted public key data.

    Returns:
        rsa.RSAPublicKey: The deserialized RSA public key.
    """
    return serialization.load_pem_public_key(pem_data)


def validate_private_key_password(
    private_key_pem: bytes,
    password: bytes,
) -> bool:
    """
    Validate if the provided password can decrypt the private key.

    Parameters:
        private_key_pem (bytes): The PEM-formatted private key data.
        password (bytes): The password to validate.

    Returns:
        bool: True if the password is correct, False otherwise.
    """
    if not isinstance(password, bytes):
        password = password.encode("utf-8")
    if not isinstance(private_key_pem, bytes):
        private_key_pem = private_key_pem.encode("utf-8")

    return bool(deserialize_private_key_from_pem(private_key_pem, password))


def encrypt_string(
    public_key: rsa.RSAPublicKey,
    plaintext: str,
    aes_key: Optional[bytes] = None,
    aesgcm: Optional[AESGCM] = None,
) -> str:
    """
    Encrypt arbitrarily large strings using hybrid RSA + AES-GCM.

    Parameters:
        public_key (rsa.RSAPublicKey): The RSA public key for encrypting the AES key.
        plaintext (str): The plaintext string to encrypt.
        aes_key (Optional[bytes]): Existing AES key to reuse.
        aesgcm (Optional[AESGCM]): Existing AESGCM instance to reuse.

    Returns:
        str: The encrypted data, represented as a hex string (safe for DB text columns).
    """

    if aes_key is None:
        aes_key = AESGCM.generate_key(bit_length=256)
    if aesgcm is None:
        aesgcm = AESGCM(aes_key)

    nonce = os.urandom(12)
    plaintext_bytes = (
        plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    )
    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    return (encrypted_key + nonce + ciphertext).hex()

async def encrypt_async_byte_stream_to_file(
    public_key: rsa.RSAPublicKey,
    byte_stream,
    output_filepath: str,
    chunk_size: int = 1024 * 1024,
) -> None:
    """
    Encrypt an async byte stream directly to disk using compact v2 file encryption.

    This function keeps plaintext chunks at chunk_size without accumulating the
    full request body in memory.
    """

    aes_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(aes_key)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    total_plaintext_size = 0
    pending = b""

    def write_encrypted_chunk(fout, plaintext_chunk: bytes) -> None:
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext_chunk, None)

        fout.write(struct.pack(">I", 12 + len(ciphertext)))
        fout.write(nonce)
        fout.write(ciphertext)

    with open(output_filepath, "wb") as fout:
        fout.write(struct.pack(">Q", 0))
        fout.write(FILE_MAGIC_V2)
        fout.write(struct.pack(">H", len(encrypted_key)))
        fout.write(encrypted_key)

        async for incoming_chunk in byte_stream:
            if not incoming_chunk:
                continue

            data = pending + incoming_chunk
            offset = 0
            data_length = len(data)

            while data_length - offset >= chunk_size:
                plaintext_chunk = data[offset : offset + chunk_size]
                offset += chunk_size

                total_plaintext_size += len(plaintext_chunk)
                write_encrypted_chunk(fout, plaintext_chunk)

            pending = data[offset:]

        if pending:
            total_plaintext_size += len(pending)
            write_encrypted_chunk(fout, pending)

        fout.seek(0)
        fout.write(struct.pack(">Q", total_plaintext_size))


def decrypt_string(
    private_key: rsa.RSAPrivateKey,
    blob: str,
) -> str:
    """
    Decrypt data encrypted by encrypt_string().

    Parameters:
        private_key (rsa.RSAPrivateKey): The RSA private key for decrypting the AES key.
        blob (str): The encrypted data as a hex string.

    Returns:
        str: The decrypted plaintext string.
    """

    blob_bytes = bytes.fromhex(blob) if isinstance(blob, str) else blob
    rsa_key_size_bytes = private_key.key_size // 8

    encrypted_key = blob_bytes[:rsa_key_size_bytes]
    nonce = blob_bytes[rsa_key_size_bytes : rsa_key_size_bytes + 12]
    ciphertext = blob_bytes[rsa_key_size_bytes + 12 :]

    aes_key = private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    plaintext = AESGCM(aes_key).decrypt(nonce, ciphertext, None)

    return plaintext.decode("utf-8")


def encrypt_data_to_file(
    public_key: rsa.RSAPublicKey,
    input_bytes: bytes,
    output_filepath: str,
    chunk_size: int = 1024 * 1024,
) -> None:
    """
    Encrypt bytes to disk using the compact v2 binary file format.

    This function keeps the old public API for callers that already have bytes
    in memory, but it no longer uses encrypt_string() for file contents.

    Old behavior expanded files by roughly 4x because it did:

        binary -> hex -> AES -> hex text

    New behavior encrypts binary chunks directly with AES-GCM and RSA-wraps the
    AES key once per file.
    """

    encrypt_stream_to_file(
        public_key=public_key,
        input_stream=io.BytesIO(input_bytes),
        output_filepath=output_filepath,
        chunk_size=chunk_size,
    )


def encrypt_stream_to_file(
    public_key: rsa.RSAPublicKey,
    input_stream: BinaryIO,
    output_filepath: str,
    chunk_size: int = 1024 * 1024,
) -> None:
    """
    Stream-encrypt binary data to disk using compact v2 file encryption.

    This avoids:
    - loading the whole uploaded file into memory
    - converting file bytes to hex
    - RSA-encrypting the AES key once per chunk
    """

    aes_key = AESGCM.generate_key(bit_length=256)
    aesgcm = AESGCM(aes_key)

    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    total_plaintext_size = 0

    with open(output_filepath, "wb") as fout:
        # Placeholder. Updated after all chunks have been streamed.
        fout.write(struct.pack(">Q", 0))

        fout.write(FILE_MAGIC_V2)
        fout.write(struct.pack(">H", len(encrypted_key)))
        fout.write(encrypted_key)

        while True:
            chunk = input_stream.read(chunk_size)
            if not chunk:
                break

            total_plaintext_size += len(chunk)

            nonce = os.urandom(12)
            ciphertext = aesgcm.encrypt(nonce, chunk, None)
            payload = nonce + ciphertext

            fout.write(struct.pack(">I", len(payload)))
            fout.write(payload)

        fout.seek(0)
        fout.write(struct.pack(">Q", total_plaintext_size))


def decrypt_data_from_file(
    private_key: rsa.RSAPrivateKey,
    input_filepath: str,
    start_chunk: int = 0,
    end_chunk: Optional[int] = None,
) -> Iterator[bytes]:
    """
    Decrypt a file encrypted by either:

    - old v1 format from encrypt_data_to_file()
      The old format stores each chunk as a UTF-8 encoded hex string created by
      encrypt_string(public_key, chunk.hex(), ...).

    - new v2 format from encrypt_stream_to_file()
      The new format stores binary AES-GCM chunks and wraps the AES key once per file.

    Yields plaintext binary chunks.
    """

    with open(input_filepath, "rb") as fin:
        original_size_bytes = fin.read(8)
        if len(original_size_bytes) != 8:
            raise ValueError("Unexpected end of file while reading original file size")

        marker = fin.read(len(FILE_MAGIC_V2))

        if marker == FILE_MAGIC_V2:
            yield from _decrypt_data_from_file_v2(
                private_key=private_key,
                fin=fin,
                start_chunk=start_chunk,
                end_chunk=end_chunk,
            )
        else:
            # Old files do not have a magic marker. Rewind to right after the
            # original 8-byte plaintext size and parse using the old format.
            fin.seek(8)

            yield from _decrypt_data_from_file_v1(
                private_key=private_key,
                fin=fin,
                start_chunk=start_chunk,
                end_chunk=end_chunk,
            )


def _decrypt_data_from_file_v1(
    private_key: rsa.RSAPrivateKey,
    fin: BinaryIO,
    start_chunk: int = 0,
    end_chunk: Optional[int] = None,
) -> Iterator[bytes]:
    """
    Decrypt old v1 encrypted files.

    Old v1 chunk format after the 8-byte original size header:

        repeated chunks:
            4 bytes: encrypted text length
            N bytes: UTF-8 encoded hex string from encrypt_string(...)

    This fallback keeps already encrypted files readable.
    """

    chunk_index = 0

    while chunk_index < start_chunk:
        length_bytes = fin.read(4)
        if not length_bytes:
            return

        if len(length_bytes) != 4:
            raise ValueError("Unexpected end of file while reading encrypted chunk length")

        (chunk_length,) = struct.unpack(">I", length_bytes)
        fin.seek(chunk_length, 1)
        chunk_index += 1

    while True:
        length_bytes = fin.read(4)
        if not length_bytes:
            break

        if len(length_bytes) != 4:
            raise ValueError("Unexpected end of file while reading encrypted chunk length")

        (chunk_length,) = struct.unpack(">I", length_bytes)
        encrypted_chunk = fin.read(chunk_length)

        if len(encrypted_chunk) != chunk_length:
            raise ValueError("Unexpected end of file while reading encrypted chunk")

        if end_chunk is not None and chunk_index > end_chunk:
            break

        encrypted_text = encrypted_chunk.decode("utf-8")
        decrypted_hex = decrypt_string(private_key, encrypted_text)

        yield bytes.fromhex(decrypted_hex)

        chunk_index += 1

def _decrypt_data_from_file_v2(
    private_key: rsa.RSAPrivateKey,
    fin: BinaryIO,
    start_chunk: int = 0,
    end_chunk: Optional[int] = None,
) -> Iterator[bytes]:
    """
    Decrypt new compact v2 encrypted files.

    v2 format after the 8-byte original size header and FILE_MAGIC_V2 marker:

        2 bytes: RSA-encrypted AES key length
        N bytes: RSA-encrypted AES key
        repeated chunks:
            4 bytes: payload length
            payload:
                12 bytes: AES-GCM nonce
                remaining bytes: AES-GCM ciphertext including tag
    """

    key_length_bytes = fin.read(2)
    if len(key_length_bytes) != 2:
        raise ValueError("Unexpected end of file while reading encrypted key length")

    (encrypted_key_length,) = struct.unpack(">H", key_length_bytes)

    encrypted_key = fin.read(encrypted_key_length)
    if len(encrypted_key) != encrypted_key_length:
        raise ValueError("Unexpected end of file while reading encrypted key")

    aes_key = private_key.decrypt(
        encrypted_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    aesgcm = AESGCM(aes_key)
    chunk_index = 0

    while True:
        length_bytes = fin.read(4)
        if not length_bytes:
            break

        if len(length_bytes) != 4:
            raise ValueError("Unexpected end of file while reading encrypted chunk length")

        (payload_length,) = struct.unpack(">I", length_bytes)

        if chunk_index < start_chunk:
            fin.seek(payload_length, 1)
            chunk_index += 1
            continue

        if end_chunk is not None and chunk_index > end_chunk:
            break

        payload = fin.read(payload_length)
        if len(payload) != payload_length:
            raise ValueError("Unexpected end of file while reading encrypted chunk")

        if len(payload) < 13:
            raise ValueError("Encrypted chunk payload is too short")

        nonce = payload[:12]
        ciphertext = payload[12:]

        yield aesgcm.decrypt(nonce, ciphertext, None)

        chunk_index += 1


def get_encrypted_file_size(
    input_filepath: str,
) -> int:
    """
    Get the original file size stored in an encrypted file.

    Parameters:
        input_filepath (str): The path to the encrypted file.

    Returns:
        int: The original file size in bytes.
    """

    with open(input_filepath, "rb") as fin:
        length_bytes = fin.read(8)

        if len(length_bytes) != 8:
            raise ValueError("Unexpected end of file while reading original file size")

        return struct.unpack(">Q", length_bytes)[0]


def get_encrypted_file_actual_size(
    input_filepath: str,
    chunk_size: int,
) -> int:
    """
    Get the actual available plaintext size based on chunks present in the
    encrypted file.

    Supports both:
    - old v1 hex-heavy encrypted files
    - new v2 compact binary encrypted files

    This function does not decrypt chunks. It counts complete encrypted chunks
    and calculates the available plaintext size from the original size header
    and configured plaintext chunk size.
    """

    with open(input_filepath, "rb") as fin:
        original_size_bytes = fin.read(8)
        if len(original_size_bytes) != 8:
            return 0

        original_size = struct.unpack(">Q", original_size_bytes)[0]

        marker = fin.read(len(FILE_MAGIC_V2))

        if marker == FILE_MAGIC_V2:
            key_length_bytes = fin.read(2)
            if len(key_length_bytes) != 2:
                return 0

            (encrypted_key_length,) = struct.unpack(">H", key_length_bytes)
            fin.seek(encrypted_key_length, 1)
        else:
            # Old v1 format has no magic marker. Go back to right after the
            # 8-byte original size header.
            fin.seek(8)

        chunk_count = 0

        while True:
            length_bytes = fin.read(4)
            if not length_bytes:
                break

            if len(length_bytes) != 4:
                break

            (encrypted_chunk_length,) = struct.unpack(">I", length_bytes)

            current_position = fin.tell()
            fin.seek(encrypted_chunk_length, 1)

            # If seeking went beyond EOF, the last chunk is incomplete and should
            # not be counted.
            if fin.tell() - current_position != encrypted_chunk_length:
                break

            chunk_count += 1

        if chunk_count == 0:
            return 0

        expected_total_chunks = (original_size + chunk_size - 1) // chunk_size

        if chunk_count >= expected_total_chunks:
            return original_size

        complete_chunks = chunk_count - 1
        last_chunk_original_offset = complete_chunks * chunk_size

        if last_chunk_original_offset >= original_size:
            return complete_chunks * chunk_size

        remaining_bytes = min(chunk_size, original_size - last_chunk_original_offset)

        return complete_chunks * chunk_size + remaining_bytes
