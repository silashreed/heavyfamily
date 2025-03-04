from decimal import Decimal
from typing import Dict, Any, Optional, List, Union, Set
import logging
import json
import os
import asyncio
from pathlib import Path
import time
import base64
import uuid
import shutil
from threading import Lock

# Security imports for wallet data encryption
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    logging.warning("Cryptography package not available. Secure wallet storage disabled.")

from ..bridge.agentkit_bridge import AgentKitBridge, AgentKitError

logger = logging.getLogger(__name__)

class WalletSecurityManager:
    """
    Manages wallet security, including encryption of sensitive wallet data.
    """
    
    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize wallet security manager.
        
        Args:
            encryption_key: Key used for wallet data encryption
        """
        self.encryption_key = encryption_key or os.getenv("HEAVY_WALLET_ENCRYPTION_KEY")
        self.fernet = None
        
        if self.encryption_key and ENCRYPTION_AVAILABLE:
            self._setup_encryption()
        elif not ENCRYPTION_AVAILABLE:
            logger.warning("Encryption not available. Wallet data will not be encrypted.")
        elif not self.encryption_key:
            logger.warning("No encryption key provided. Wallet data will not be encrypted.")
    
    def _setup_encryption(self):
        """Set up encryption using the provided key."""
        # Convert passphrase to encryption key
        salt = b'heavywallet'  # In production, use a securely stored salt
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.encryption_key.encode()))
        self.fernet = Fernet(key)
    
    def encrypt_wallet_data(self, wallet_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Encrypt sensitive wallet data.
        
        Args:
            wallet_data: Wallet data to encrypt
            
        Returns:
            Wallet data with sensitive fields encrypted
        """
        if not self.fernet:
            return wallet_data
        
        try:
            # Make a copy of the wallet data
            data = wallet_data.copy()
            
            # Extract sensitive data
            sensitive_data = {
                "private_key": data.pop("private_key", None),
                "mnemonic": data.pop("mnemonic", None),
                "seed": data.pop("seed", None)
            }
            
            # Only encrypt if there's sensitive data
            if any(sensitive_data.values()):
                # Encrypt sensitive data
                encrypted_data = self.fernet.encrypt(json.dumps(sensitive_data).encode())
                data["_encrypted"] = encrypted_data.decode()
            
            return data
        except Exception as e:
            logger.error(f"Failed to encrypt wallet data: {str(e)}")
            return wallet_data
    
    def decrypt_wallet_data(self, wallet_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Decrypt sensitive wallet data.
        
        Args:
            wallet_data: Wallet data with encrypted fields
            
        Returns:
            Wallet data with sensitive fields decrypted
        """
        if not self.fernet or "_encrypted" not in wallet_data:
            return wallet_data
        
        try:
            # Make a copy of the wallet data
            data = wallet_data.copy()
            
            # Extract encrypted data
            encrypted_data = data.pop("_encrypted", None)
            
            if encrypted_data:
                # Decrypt sensitive data
                decrypted_data = self.fernet.decrypt(encrypted_data.encode())
                sensitive_data = json.loads(decrypted_data.decode())
                
                # Merge sensitive data back into wallet data
                data.update(sensitive_data)
            
            return data
        except Exception as e:
            logger.error(f"Failed to decrypt wallet data: {str(e)}")
            return wallet_data
    
    def secure_delete_file(self, file_path: str) -> bool:
        """
        Securely delete a file by overwriting it before deletion.
        
        Args:
            file_path: Path to the file to delete
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if file exists
            if not os.path.exists(file_path):
                return True
            
            # Get file size
            file_size = os.path.getsize(file_path)
            
            # Overwrite with random data
            with open(file_path, 'wb') as f:
                f.write(os.urandom(file_size))
            
            # Overwrite with zeros
            with open(file_path, 'wb') as f:
                f.write(b'\0' * file_size)
            
            # Delete the file
            os.remove(file_path)
            
            return True
        except Exception as e:
            logger.error(f"Failed to securely delete file {file_path}: {str(e)}")
            return False

class WalletStateManager:
    """
    Manages wallet state, ensuring isolation and data integrity.
    """
    
    def __init__(self, wallets_dir: str):
        """
        Initialize wallet state manager.
        
        Args:
            wallets_dir: Directory for wallet state storage
        """
        self.wallets_dir = os.path.expanduser(wallets_dir)
        self.lock = Lock()
        self.changed_wallets: Set[str] = set()
        
        # Ensure wallets directory exists
        os.makedirs(self.wallets_dir, exist_ok=True)
    
    def _get_wallet_path(self, wallet_id: str) -> str:
        """Get the file path for a wallet."""
        # Sanitize wallet_id to ensure it's a valid filename
        sanitized_id = "".join([c for c in wallet_id if c.isalnum() or c in "-_."]).rstrip()
        if not sanitized_id:
            sanitized_id = str(uuid.uuid4())
        
        return os.path.join(self.wallets_dir, f"{sanitized_id}.json")
    
    def load_wallet(self, wallet_id: str) -> Optional[Dict[str, Any]]:
        """
        Load wallet state from disk.
        
        Args:
            wallet_id: ID of the wallet to load
            
        Returns:
            Wallet data if found, None otherwise
        """
        wallet_path = self._get_wallet_path(wallet_id)
        
        if not os.path.exists(wallet_path):
            return None
        
        try:
            with open(wallet_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load wallet {wallet_id}: {str(e)}")
            return None
    
    def save_wallet(self, wallet_id: str, wallet_data: Dict[str, Any]) -> bool:
        """
        Save wallet state to disk.
        
        Args:
            wallet_id: ID of the wallet to save
            wallet_data: Wallet data to save
            
        Returns:
            True if successful, False otherwise
        """
        wallet_path = self._get_wallet_path(wallet_id)
        
        try:
            # Create a temporary file
            temp_path = f"{wallet_path}.tmp"
            
            with open(temp_path, 'w') as f:
                json.dump(wallet_data, f, indent=2)
            
            # Atomically replace the old file
            with self.lock:
                shutil.move(temp_path, wallet_path)
                self.changed_wallets.add(wallet_id)
            
            return True
        except Exception as e:
            logger.error(f"Failed to save wallet {wallet_id}: {str(e)}")
            return False
    
    def delete_wallet(self, wallet_id: str, secure: bool = True) -> bool:
        """
        Delete wallet state from disk.
        
        Args:
            wallet_id: ID of the wallet to delete
            secure: Whether to securely delete the wallet file
            
        Returns:
            True if successful, False otherwise
        """
        wallet_path = self._get_wallet_path(wallet_id)
        
        if not os.path.exists(wallet_path):
            return True
        
        try:
            with self.lock:
                if secure and ENCRYPTION_AVAILABLE:
                    security_manager = WalletSecurityManager()
                    security_manager.secure_delete_file(wallet_path)
                else:
                    os.remove(wallet_path)
                
                self.changed_wallets.discard(wallet_id)
            
            return True
        except Exception as e:
            logger.error(f"Failed to delete wallet {wallet_id}: {str(e)}")
            return False
    
    def list_wallets(self) -> List[str]:
        """
        List all wallet IDs.
        
        Returns:
            List of wallet IDs
        """
        try:
            wallet_files = [f for f in os.listdir(self.wallets_dir) if f.endswith('.json')]
            return [os.path.splitext(f)[0] for f in wallet_files]
        except Exception as e:
            logger.error(f"Failed to list wallets: {str(e)}")
            return []
    
    def backup_wallets(self, backup_dir: Optional[str] = None) -> bool:
        """
        Backup all wallets to a backup directory.
        
        Args:
            backup_dir: Directory to store backups (defaults to wallets_dir/backups/timestamp)
            
        Returns:
            True if successful, False otherwise
        """
        if not backup_dir:
            timestamp = int(time.time())
            backup_dir = os.path.join(self.wallets_dir, "backups", str(timestamp))
        
        try:
            # Ensure backup directory exists
            os.makedirs(backup_dir, exist_ok=True)
            
            # Copy all wallet files
            wallet_files = [f for f in os.listdir(self.wallets_dir) if f.endswith('.json')]
            
            for file_name in wallet_files:
                src_path = os.path.join(self.wallets_dir, file_name)
                dst_path = os.path.join(backup_dir, file_name)
                shutil.copy2(src_path, dst_path)
            
            logger.info(f"Backed up {len(wallet_files)} wallets to {backup_dir}")
            return True
        except Exception as e:
            logger.error(f"Failed to backup wallets: {str(e)}")
            return False

class HeavyWalletAdapter:
    """
    Adapter for Heavy's wallet system to work with AgentKit.
    
    This class adapts Heavy's wallet management to work with
    AgentKit's interfaces, handling persistence, wallet creation,
    and transaction operations.
    """
    
    def __init__(
        self,
        bridge: AgentKitBridge,
        wallets_dir: Optional[str] = None,
        default_network: str = "base-sepolia",
        encrypt_wallet_data: bool = False,
        encryption_key: Optional[str] = None
    ) -> None:
        """
        Initialize the wallet adapter.
        
        Args:
            bridge: AgentKit bridge instance
            wallets_dir: Directory for storing wallet data
            default_network: Default network for wallets
            encrypt_wallet_data: Whether to encrypt sensitive wallet data
            encryption_key: Key for wallet data encryption
        """
        self.bridge = bridge
        self.default_network = default_network
        
        # Set up wallet storage directory
        if wallets_dir:
            self.wallets_dir = Path(os.path.expanduser(wallets_dir))
        else:
            home_dir = Path.home()
            self.wallets_dir = home_dir / ".heavy" / "wallets"
            
        # Create wallets directory if it doesn't exist
        os.makedirs(self.wallets_dir, exist_ok=True)
        
        # Initialize state and security managers
        self.state_manager = WalletStateManager(str(self.wallets_dir))
        self.security_manager = None
        
        if encrypt_wallet_data:
            self.security_manager = WalletSecurityManager(encryption_key)
        
        # Load existing wallets
        self.wallets = self._load_wallets()
        
        logger.info(f"Initialized HeavyWalletAdapter with {len(self.wallets)} wallets")
    
    def _load_wallets(self) -> Dict[str, Dict[str, Any]]:
        """
        Load wallet data from the wallets directory.
        
        Returns:
            Dictionary of wallet IDs to wallet data
        """
        wallets = {}
        try:
            # Load all JSON files in the wallets directory
            for file_path in self.wallets_dir.glob("*.json"):
                try:
                    with open(file_path, "r") as f:
                        wallet_data = json.load(f)
                        
                    if "id" in wallet_data:
                        wallet_id = wallet_data["id"]
                        wallets[wallet_id] = wallet_data
                        logger.debug(f"Loaded wallet {wallet_id} from {file_path}")
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"Failed to load wallet from {file_path}: {str(e)}")
            
            logger.info(f"Loaded {len(wallets)} wallets from {self.wallets_dir}")
            return wallets
        except Exception as e:
            logger.error(f"Error loading wallets: {str(e)}")
            return {}
    
    def _save_wallet(self, wallet_id: str, wallet_data: Dict[str, Any]) -> bool:
        """
        Save wallet data to disk.
        
        Args:
            wallet_id: Wallet ID
            wallet_data: Wallet data to save
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure wallet_id is in the data
            if "id" not in wallet_data:
                wallet_data["id"] = wallet_id
                
            # Encrypt sensitive wallet data if encryption is enabled
            if self.security_manager:
                wallet_data = self.security_manager.encrypt_wallet_data(wallet_data)
            
            # Create filename from wallet ID (sanitized)
            safe_id = wallet_id.replace("/", "_").replace(":", "_")
            file_path = self.wallets_dir / f"{safe_id}.json"
            
            # Save wallet data to file
            with open(file_path, "w") as f:
                json.dump(wallet_data, f, indent=2)
                
            logger.debug(f"Saved wallet {wallet_id} to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save wallet {wallet_id}: {str(e)}")
            return False
    
    async def create_wallet(
        self,
        name: Optional[str] = None,
        network: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new wallet.
        
        Args:
            name: Optional wallet name
            network: Optional network ID (defaults to default_network)
            
        Returns:
            Wallet information or None if creation failed
        """
        try:
            # Use the bridge to create a wallet
            wallet_data = await self.bridge.create_wallet(name)
            
            if not wallet_data:
                logger.error("Bridge returned no wallet data")
                return None
                
            wallet_id = wallet_data["id"]
            
            # Add any additional Heavy-specific data
            wallet_data["created_at"] = self._get_timestamp()
            wallet_data["last_updated"] = self._get_timestamp()
            
            # Store the wallet data
            self.wallets[wallet_id] = wallet_data
            self._save_wallet(wallet_id, wallet_data)
            
            logger.info(f"Created wallet: {wallet_id}")
            return wallet_data
        except AgentKitError as e:
            logger.error(f"AgentKit error creating wallet: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating wallet: {str(e)}")
            return None
    
    async def import_wallet(self, wallet_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Import an existing wallet.
        
        Args:
            wallet_data: Wallet data for import
            
        Returns:
            Imported wallet information or None if import failed
        """
        try:
            # Use the bridge to import the wallet
            imported_wallet = await self.bridge.import_wallet(wallet_data)
            
            if not imported_wallet:
                logger.error("Bridge returned no wallet data")
                return None
                
            wallet_id = imported_wallet["id"]
            
            # Add any additional Heavy-specific data
            imported_wallet["imported_at"] = self._get_timestamp()
            imported_wallet["last_updated"] = self._get_timestamp()
            
            # Store the wallet data
            self.wallets[wallet_id] = imported_wallet
            self._save_wallet(wallet_id, imported_wallet)
            
            logger.info(f"Imported wallet: {wallet_id}")
            return imported_wallet
        except AgentKitError as e:
            logger.error(f"AgentKit error importing wallet: {e}")
            return None
        except Exception as e:
            logger.error(f"Error importing wallet: {str(e)}")
            return None
    
    def list_wallets(self) -> List[Dict[str, Any]]:
        """
        List all wallets.
        
        Returns:
            List of wallet information
        """
        return list(self.wallets.values())
    
    def get_wallet(self, wallet_id: str) -> Optional[Dict[str, Any]]:
        """
        Get wallet information.
        
        Args:
            wallet_id: Wallet ID
            
        Returns:
            Wallet information or None if not found
        """
        return self.wallets.get(wallet_id)
    
    async def get_balance(
        self,
        wallet_id: str,
        token: str = "eth",
        address_id: Optional[str] = None
    ) -> Optional[Decimal]:
        """
        Get token balance for a wallet.
        
        Args:
            wallet_id: Wallet ID
            token: Token address or symbol
            address_id: Optional specific address ID
            
        Returns:
            Token balance as Decimal or None if retrieval failed
        """
        try:
            # Use the bridge to get the balance
            balance = await self.bridge.get_balance(wallet_id, token)
            
            if balance is not None:
                # Update the wallet's cached balance data if we have it
                if wallet_id in self.wallets:
                    if "balances" not in self.wallets[wallet_id]:
                        self.wallets[wallet_id]["balances"] = {}
                    
                    self.wallets[wallet_id]["balances"][token] = str(balance)
                    self.wallets[wallet_id]["last_updated"] = self._get_timestamp()
                    self._save_wallet(wallet_id, self.wallets[wallet_id])
            
            return balance
        except AgentKitError as e:
            logger.error(f"AgentKit error getting balance: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting balance: {str(e)}")
            return None
    
    async def transfer(
        self,
        wallet_id: str,
        amount: Union[str, float, Decimal],
        token: str,
        to_address: str,
        from_address: Optional[str] = None,
        gasless: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Transfer tokens from a wallet.
        
        Args:
            wallet_id: Source wallet ID
            amount: Amount to transfer
            token: Token address or symbol
            to_address: Recipient address
            from_address: Optional specific source address
            gasless: Whether to use gasless transactions
            
        Returns:
            Transaction information or None if transfer failed
        """
        try:
            # Use the bridge to perform the transfer
            transaction = await self.bridge.transfer(
                wallet_id=wallet_id,
                amount=amount,
                token_address=token,
                to_address=to_address,
                from_address=from_address,
                gasless=gasless
            )
            
            if not transaction:
                logger.error("Bridge returned no transaction data")
                return None
                
            # Get the transaction ID
            tx_id = transaction.get("id")
            
            # Update the wallet's transaction history if we have it
            if wallet_id in self.wallets:
                if "transactions" not in self.wallets[wallet_id]:
                    self.wallets[wallet_id]["transactions"] = []
                
                # Add the transaction to the wallet's history
                self.wallets[wallet_id]["transactions"].append({
                    "id": tx_id,
                    "type": "transfer",
                    "token": token,
                    "amount": str(amount) if isinstance(amount, (Decimal, float)) else amount,
                    "to": to_address,
                    "from": from_address,
                    "timestamp": self._get_timestamp(),
                    "status": transaction.get("status", "unknown"),
                    "transaction_hash": transaction.get("transaction_hash")
                })
                
                # Update the wallet's last updated timestamp
                self.wallets[wallet_id]["last_updated"] = self._get_timestamp()
                self._save_wallet(wallet_id, self.wallets[wallet_id])
            
            logger.info(f"Transfer completed: {tx_id}")
            return transaction
        except AgentKitError as e:
            logger.error(f"AgentKit error during transfer: {e}")
            return None
        except Exception as e:
            logger.error(f"Error during transfer: {str(e)}")
            return None
    
    async def trade(
        self,
        wallet_id: str,
        amount: Union[str, float, Decimal],
        from_token: str,
        to_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Trade tokens.
        
        Args:
            wallet_id: Wallet ID
            amount: Amount to trade
            from_token: Source token address or symbol
            to_token: Target token address or symbol
            
        Returns:
            Transaction information or None if trade failed
        """
        try:
            # Use the bridge to perform the trade
            transaction = await self.bridge.trade(
                wallet_id=wallet_id,
                amount=amount,
                from_token=from_token,
                to_token=to_token
            )
            
            if not transaction:
                logger.error("Bridge returned no transaction data")
                return None
                
            # Get the transaction ID
            tx_id = transaction.get("id")
            
            # Update the wallet's transaction history if we have it
            if wallet_id in self.wallets:
                if "transactions" not in self.wallets[wallet_id]:
                    self.wallets[wallet_id]["transactions"] = []
                
                # Add the transaction to the wallet's history
                self.wallets[wallet_id]["transactions"].append({
                    "id": tx_id,
                    "type": "trade",
                    "from_token": from_token,
                    "to_token": to_token,
                    "amount": str(amount) if isinstance(amount, (Decimal, float)) else amount,
                    "timestamp": self._get_timestamp(),
                    "status": transaction.get("status", "unknown"),
                    "transaction_hash": transaction.get("transaction_hash")
                })
                
                # Update the wallet's last updated timestamp
                self.wallets[wallet_id]["last_updated"] = self._get_timestamp()
                self._save_wallet(wallet_id, self.wallets[wallet_id])
            
            logger.info(f"Trade completed: {tx_id}")
            return transaction
        except AgentKitError as e:
            logger.error(f"AgentKit error during trade: {e}")
            return None
        except Exception as e:
            logger.error(f"Error during trade: {str(e)}")
            return None
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in seconds."""
        return int(time.time()) 