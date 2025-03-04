from decimal import Decimal
from typing import Dict, Any, Optional, List, Union, Tuple, Callable, Awaitable
import logging
import json
import os
import asyncio
from pathlib import Path
import time
from dotenv import load_dotenv
import base64

# Security imports for credential management
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    logging.warning("Cryptography package not available. Secure credential storage disabled.")

try:
    from coinbase_agentkit import (
        AgentKit as RealAgentKit,
        AgentKitConfig,
        CdpWalletProvider,
        CdpWalletProviderConfig,
        WalletActionProvider
    )
    AGENTKIT_AVAILABLE = True
except ImportError:
    AGENTKIT_AVAILABLE = False
    logging.warning("coinbase-agentkit SDK not available. Using mock implementation.")

logger = logging.getLogger(__name__)

class CredentialManager:
    """
    Manages secure storage and retrieval of credentials.
    Supports encrypted storage and credential rotation.
    """
    
    def __init__(self, 
                 encryption_key: Optional[str] = None, 
                 credentials_file: Optional[str] = None):
        """
        Initialize the credential manager.
        
        Args:
            encryption_key: Key used for encrypting stored credentials
            credentials_file: Path to encrypted credentials storage
        """
        self.encryption_key = encryption_key or os.getenv("HEAVY_ENCRYPTION_KEY")
        self.credentials_file = credentials_file or os.path.expanduser("~/.heavy/credentials/encrypted_credentials.bin")
        
        # Ensure credentials directory exists
        os.makedirs(os.path.dirname(self.credentials_file), exist_ok=True)
        
        # Set up encryption if key is available
        self.fernet = None
        if self.encryption_key and ENCRYPTION_AVAILABLE:
            self._setup_encryption()
        elif not ENCRYPTION_AVAILABLE:
            logger.warning("Encryption not available. Credentials will not be securely stored.")
        elif not self.encryption_key:
            logger.warning("No encryption key provided. Credentials will not be securely stored.")
    
    def _setup_encryption(self):
        """Set up encryption using the provided key."""
        # Convert passphrase to encryption key
        salt = b'heavysalt'  # In production, use a securely stored salt
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.encryption_key.encode()))
        self.fernet = Fernet(key)
    
    def store_credentials(self, credentials: Dict[str, str]) -> bool:
        """
        Securely store credentials.
        
        Args:
            credentials: Dictionary of credentials to store
            
        Returns:
            True if successful, False otherwise
        """
        if not self.fernet:
            logger.warning("Encryption not set up. Cannot securely store credentials.")
            return False
        
        try:
            # Serialize and encrypt
            data = json.dumps(credentials).encode()
            encrypted_data = self.fernet.encrypt(data)
            
            # Write to file
            with open(self.credentials_file, 'wb') as f:
                f.write(encrypted_data)
            
            logger.info("Credentials securely stored")
            return True
        except Exception as e:
            logger.error(f"Failed to store credentials: {str(e)}")
            return False
    
    def retrieve_credentials(self) -> Optional[Dict[str, str]]:
        """
        Retrieve stored credentials.
        
        Returns:
            Dictionary of credentials if successful, None otherwise
        """
        if not self.fernet:
            logger.warning("Encryption not set up. Cannot retrieve credentials.")
            return None
        
        if not os.path.exists(self.credentials_file):
            logger.warning(f"Credentials file not found: {self.credentials_file}")
            return None
        
        try:
            # Read and decrypt
            with open(self.credentials_file, 'rb') as f:
                encrypted_data = f.read()
            
            decrypted_data = self.fernet.decrypt(encrypted_data)
            credentials = json.loads(decrypted_data.decode())
            
            return credentials
        except Exception as e:
            logger.error(f"Failed to retrieve credentials: {str(e)}")
            return None
    
    def rotate_credentials(self, new_credentials: Dict[str, str]) -> bool:
        """
        Rotate credentials by storing new credentials and backing up old ones.
        
        Args:
            new_credentials: New credentials to store
            
        Returns:
            True if successful, False otherwise
        """
        if not self.fernet:
            logger.warning("Encryption not set up. Cannot rotate credentials.")
            return False
        
        try:
            # Backup existing credentials if they exist
            if os.path.exists(self.credentials_file):
                backup_file = f"{self.credentials_file}.{int(time.time())}.bak"
                with open(self.credentials_file, 'rb') as src, open(backup_file, 'wb') as dst:
                    dst.write(src.read())
                logger.info(f"Backed up previous credentials to {backup_file}")
            
            # Store new credentials
            return self.store_credentials(new_credentials)
        except Exception as e:
            logger.error(f"Failed to rotate credentials: {str(e)}")
            return False

class AgentKitError(Exception):
    """Custom exception for AgentKit-related errors."""
    
    def __init__(self, message: str, code: str = "unknown_error"):
        self.code = code
        super().__init__(message)

class AgentKitBridge:
    """
    Bridge for interacting with Coinbase's AgentKit SDK.
    Handles wallet operations and transactions.
    """
    
    def __init__(
        self,
        api_key_name: Optional[str] = None,
        api_key_private_key: Optional[str] = None,
        config_path: Optional[str] = None,
        network_id: str = "base-sepolia",
        use_encrypted_storage: bool = False,
        encryption_key: Optional[str] = None
    ) -> None:
        """
        Initialize the AgentKit bridge.
        
        Args:
            api_key_name: CDP API key name
            api_key_private_key: CDP API private key
            config_path: Path to configuration file
            network_id: Network ID to use (default: base-sepolia)
            use_encrypted_storage: Whether to use encrypted credential storage
            encryption_key: Key for encrypted credential storage
        """
        # Load environment variables from .env file
        load_dotenv()
        
        # Initialize credential manager if using encrypted storage
        self.credential_manager = None
        if use_encrypted_storage and ENCRYPTION_AVAILABLE:
            self.credential_manager = CredentialManager(encryption_key=encryption_key)
            stored_credentials = self.credential_manager.retrieve_credentials()
            if stored_credentials:
                api_key_name = api_key_name or stored_credentials.get("cdp_api_key_name")
                api_key_private_key = api_key_private_key or stored_credentials.get("cdp_api_key_private_key")
                logger.info("Retrieved credentials from encrypted storage")
        
        # Try environment variables if not provided
        self.api_key_name = api_key_name or os.getenv("CDP_API_KEY_NAME")
        self.api_key_private_key = api_key_private_key or os.getenv("CDP_API_KEY_PRIVATE_KEY")
        
        # If credentials are still not available, try loading from config
        if (not self.api_key_name or not self.api_key_private_key) and config_path:
            config = self._load_config(config_path)
            self.api_key_name = self.api_key_name or config.get("cdp_api_key_name")
            self.api_key_private_key = self.api_key_private_key or config.get("cdp_api_key_private_key")
            network_id = config.get("network_id", network_id)
        
        # Check if we have the required credentials
        if not self.api_key_name and not self.api_key_private_key and AGENTKIT_AVAILABLE:
            logger.warning("No CDP API credentials provided. AgentKit SDK will not be initialized.")
        
        self.network_id = network_id
        self.agent_kit = None
        self.wallet_provider = None
        self.wallet_cache = {}  # Cache wallet data to avoid repeated requests
        
        # Save credentials to encrypted storage if requested
        if use_encrypted_storage and self.credential_manager and self.api_key_name and self.api_key_private_key:
            credentials = {
                "cdp_api_key_name": self.api_key_name,
                "cdp_api_key_private_key": self.api_key_private_key
            }
            if not self.credential_manager.store_credentials(credentials):
                logger.warning("Failed to store credentials in encrypted storage")
        
        # Initialize SDK if credentials are available
        if AGENTKIT_AVAILABLE:
            if self.api_key_name and self.api_key_private_key:
                self._initialize_sdk()
            else:
                logger.warning("Running in mock mode due to missing CDP API credentials")
        
        logger.info(f"AgentKitBridge initialized with network: {network_id}")
    
    def _initialize_sdk(self) -> None:
        """Initialize the AgentKit SDK with credentials."""
        try:
            # Create a wallet provider with the CDP credentials
            self.wallet_provider = CdpWalletProvider(CdpWalletProviderConfig(
                api_key_name=self.api_key_name,
                api_key_private=self.api_key_private_key,
                network_id=self.network_id
            ))
            
            # Create the AgentKit instance with the wallet provider
            self.agent_kit = RealAgentKit(AgentKitConfig(
                wallet_provider=self.wallet_provider,
                action_providers=[WalletActionProvider()]
            ))
            
            logger.info(f"AgentKit SDK initialized with network: {self.network_id}")
        except Exception as e:
            logger.error(f"Failed to initialize AgentKit SDK: {str(e)}")
            raise AgentKitError(f"SDK initialization failed: {str(e)}", "sdk_init_error")
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """
        Load configuration from a file.
        
        Args:
            config_path: Path to the configuration file
            
        Returns:
            Configuration dictionary
        """
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {config_path}")
            return {}
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in configuration file: {config_path}")
            return {}
    
    async def create_wallet(self, name: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a new wallet.
        
        Args:
            name: Optional wallet name
            
        Returns:
            Wallet information
        """
        try:
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Create a wallet using the CDP wallet provider
            wallet = self.wallet_provider.create_wallet()
            
            # Build the response structure
            wallet_data = {
                "id": wallet.wallet_id,
                "network": self.network_id,
                "address": wallet.address,
                "name": name or f"Wallet-{self._get_timestamp()}",
                "created_at": self._get_timestamp(),
                "walletData": self.wallet_provider.export_wallet().to_dict() if hasattr(self.wallet_provider, "export_wallet") else None
            }
            
            # Cache the wallet data
            self.wallet_cache[wallet.wallet_id] = wallet_data
            
            logger.info(f"Created wallet: {wallet.wallet_id}")
            return wallet_data
        except Exception as e:
            logger.error(f"Failed to create wallet: {str(e)}")
            raise AgentKitError(f"Failed to create wallet: {str(e)}", "wallet_creation_error")
    
    async def import_wallet(self, wallet_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Import an existing wallet.
        
        Args:
            wallet_data: Wallet data for import
            
        Returns:
            Imported wallet information
        """
        try:
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Convert the wallet data to the format expected by the SDK
            wallet_data_obj = WalletData.from_dict(wallet_data.get("walletData", {}))
            
            # Import the wallet using the CDP wallet provider
            wallet = self.wallet_provider.import_wallet(wallet_data_obj)
            
            # Build the response structure
            imported_wallet = {
                "id": wallet.wallet_id,
                "network": self.network_id,
                "address": wallet.address,
                "name": wallet_data.get("name", f"Imported-{self._get_timestamp()}"),
                "created_at": wallet_data.get("created_at", self._get_timestamp()),
                "walletData": wallet_data.get("walletData")
            }
            
            # Cache the wallet data
            self.wallet_cache[wallet.wallet_id] = imported_wallet
            
            logger.info(f"Imported wallet: {wallet.wallet_id}")
            return imported_wallet
        except Exception as e:
            logger.error(f"Failed to import wallet: {str(e)}")
            raise AgentKitError(f"Failed to import wallet: {str(e)}", "wallet_import_error")
    
    async def get_wallet(self, wallet_id: str) -> Dict[str, Any]:
        """
        Get wallet information.
        
        Args:
            wallet_id: Wallet ID
            
        Returns:
            Wallet information
        """
        try:
            # Check if wallet is in cache
            if wallet_id in self.wallet_cache:
                return self.wallet_cache[wallet_id]
            
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Get the wallet from the provider
            wallet = self.wallet_provider.get_wallet(wallet_id)
            if not wallet:
                raise AgentKitError(f"Wallet not found: {wallet_id}", "wallet_not_found")
            
            # Create a response structure
            wallet_info = {
                "id": wallet.wallet_id,
                "network": self.network_id,
                "address": wallet.address,
                "name": f"Wallet-{wallet_id[:8]}",  # Use part of ID as name if not cached
                "created_at": self._get_timestamp(),
                "walletData": self.wallet_provider.export_wallet().to_dict() if hasattr(self.wallet_provider, "export_wallet") else None
            }
            
            # Cache the wallet data
            self.wallet_cache[wallet_id] = wallet_info
            
            return wallet_info
        except Exception as e:
            logger.error(f"Failed to get wallet {wallet_id}: {str(e)}")
            raise AgentKitError(f"Failed to get wallet: {str(e)}", "wallet_fetch_error")
    
    async def list_wallets(self) -> List[Dict[str, Any]]:
        """
        List all wallets.
        
        Returns:
            List of wallet information
        """
        try:
            # For now, just return cached wallets
            # In a real implementation, we would fetch all wallets from the provider
            return list(self.wallet_cache.values())
        except Exception as e:
            logger.error(f"Failed to list wallets: {str(e)}")
            raise AgentKitError(f"Failed to list wallets: {str(e)}", "wallet_list_error")
    
    async def get_balance(
        self,
        wallet_id: str,
        token_address: str = "eth"
    ) -> Optional[Decimal]:
        """
        Get token balance for a wallet.
        
        Args:
            wallet_id: Wallet ID
            token_address: Token address or symbol (default: "eth")
            
        Returns:
            Token balance as Decimal
        """
        try:
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Get the wallet from the provider
            wallet = self.wallet_provider.get_wallet(wallet_id)
            if not wallet:
                raise AgentKitError(f"Wallet not found: {wallet_id}", "wallet_not_found")
            
            # Get the balance for the specified token
            balance = wallet.get_balance(token_address)
            if balance is None:
                return Decimal(0)
            
            return Decimal(str(balance))
        except Exception as e:
            logger.error(f"Failed to get balance for wallet {wallet_id}: {str(e)}")
            raise AgentKitError(f"Failed to get balance: {str(e)}", "balance_fetch_error")
    
    async def transfer(
        self,
        wallet_id: str,
        amount: Union[str, float, Decimal],
        token_address: str,
        to_address: str,
        from_address: Optional[str] = None,
        gasless: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Transfer tokens from a wallet.
        
        Args:
            wallet_id: Source wallet ID
            amount: Amount to transfer
            token_address: Token address or symbol
            to_address: Recipient address
            from_address: Optional specific source address
            gasless: Whether to use gasless transactions (for USDC)
            
        Returns:
            Transaction information
        """
        try:
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Get the wallet from the provider
            wallet = self.wallet_provider.get_wallet(wallet_id)
            if not wallet:
                raise AgentKitError(f"Wallet not found: {wallet_id}", "wallet_not_found")
            
            # Convert amount to the appropriate format
            if isinstance(amount, Decimal):
                amount_str = str(amount)
            else:
                amount_str = str(amount)
            
            # Perform the transfer
            transfer = wallet.transfer(
                amount=amount_str,
                asset_type=token_address,
                recipient=to_address,
                gasless=gasless
            )
            
            # Wait for the transfer to complete
            transfer.wait()
            
            # Build the response structure
            transaction = {
                "id": transfer.id if hasattr(transfer, "id") else f"tx-{self._get_timestamp()}",
                "wallet_id": wallet_id,
                "from_address": from_address or wallet.address,
                "to_address": to_address,
                "amount": amount_str,
                "token": token_address,
                "status": "completed",
                "timestamp": self._get_timestamp(),
                "transaction_hash": transfer.hash if hasattr(transfer, "hash") else None
            }
            
            logger.info(f"Transfer completed: {transaction['id']}")
            return transaction
        except Exception as e:
            logger.error(f"Failed to transfer from wallet {wallet_id}: {str(e)}")
            raise AgentKitError(f"Failed to transfer: {str(e)}", "transfer_error")
    
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
            Transaction information
        """
        try:
            if not self.wallet_provider:
                raise AgentKitError("Wallet provider not initialized", "provider_not_initialized")
            
            # Get the wallet from the provider
            wallet = self.wallet_provider.get_wallet(wallet_id)
            if not wallet:
                raise AgentKitError(f"Wallet not found: {wallet_id}", "wallet_not_found")
            
            # Convert amount to the appropriate format
            if isinstance(amount, Decimal):
                amount_str = str(amount)
            else:
                amount_str = str(amount)
            
            # Perform the trade
            trade = wallet.trade(
                amount=amount_str,
                from_token=from_token,
                to_token=to_token
            )
            
            # Wait for the trade to complete
            trade.wait()
            
            # Build the response structure
            transaction = {
                "id": trade.id if hasattr(trade, "id") else f"trade-{self._get_timestamp()}",
                "wallet_id": wallet_id,
                "from_token": from_token,
                "to_token": to_token,
                "amount": amount_str,
                "status": "completed",
                "timestamp": self._get_timestamp(),
                "transaction_hash": trade.hash if hasattr(trade, "hash") else None
            }
            
            logger.info(f"Trade completed: {transaction['id']}")
            return transaction
        except Exception as e:
            logger.error(f"Failed to trade for wallet {wallet_id}: {str(e)}")
            raise AgentKitError(f"Failed to trade: {str(e)}", "trade_error")
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in seconds."""
        return int(time.time())

    def rotate_credentials(self, new_api_key_name: str, new_api_key_private_key: str) -> bool:
        """
        Rotate API credentials.
        
        Args:
            new_api_key_name: New API key name
            new_api_key_private_key: New API private key
            
        Returns:
            True if successful, False otherwise
        """
        if not self.credential_manager:
            logger.warning("Credential manager not initialized. Cannot rotate credentials.")
            return False
        
        # Store new credentials
        credentials = {
            "cdp_api_key_name": new_api_key_name,
            "cdp_api_key_private_key": new_api_key_private_key
        }
        
        if not self.credential_manager.rotate_credentials(credentials):
            logger.error("Failed to rotate credentials")
            return False
        
        # Update current credentials
        self.api_key_name = new_api_key_name
        self.api_key_private_key = new_api_key_private_key
        
        # Reinitialize SDK with new credentials
        self._initialize_sdk()
        
        logger.info("Credentials rotated successfully")
        return True 