import shutil
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
from app.core.config import get_settings
from app.services.alerting import send_system_alert

logger = logging.getLogger(__name__)

class BackupService:
    def __init__(self):
        self.settings = get_settings()
        # Extract database path from DATABASE_URL
        db_url = self.settings.database_url
        if db_url.startswith('sqlite:///'):
            self.db_path = db_url[9:]  # Remove 'sqlite:///' prefix
        else:
            # For other database types, we'd need different backup strategies
            raise ValueError(f"Unsupported database URL: {db_url}")
        
        self.backup_dir = Path(getattr(self.settings, 'backup_dir', './backups'))
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Get backup settings from settings or use defaults
        self.backup_interval_hours = getattr(self.settings, 'backup_interval_hours', 6)
        self.backup_retention_days = getattr(self.settings, 'backup_retention_days', 30)
        
        logger.info(f"BackupService initialized. DB: {self.db_path}, Backup dir: {self.backup_dir}")
    
    def create_backup(self, compress=True, encrypt=False):
        """Create a backup of the database.
        
        Args:
            compress: Whether to compress the backup (default: True)
            encrypt: Whether to encrypt the backup (default: False, requires encryption setup)
            
        Returns:
            Path to the backup file
        """ 
        try:
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"solenos_backup_{timestamp}.db"
            backup_path = self.backup_dir / backup_name
            
            # Copy the database file
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"Database backup created: {backup_path}")
            
            # Send success alert
            send_system_alert(
                component="backup_service",
                description=f"Backup completed successfully: {backup_path.name}",
                severity="info",
                details={
                    "backup_path": str(backup_path),
                    "backup_size": backup_path.stat().st_size if backup_path.exists() else 0
                }
            )
            
            # Compress if requested
            if compress:
                compressed_path = backup_path.with_suffix('.db.gz')
                import gzip
                with open(backup_path, 'rb') as f_in:
                    with gzip.open(compressed_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                # Remove uncompressed file
                backup_path.unlink()
                backup_path = compressed_path
                logger.info(f"Backup compressed: {backup_path}")
            
            # Encrypt if requested (placeholder - would need actual encryption implementation)
            if encrypt:
                logger.warning("Encryption not yet implemented. Backup remains unencrypted.")
                # In a real implementation, you would use something like:
                # encrypted_path = self._encrypt_backup(backup_path)
                # backup_path.unlink()
                # backup_path = encrypted_path
            
            return backup_path
            
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")
            
            # Send failure alert
            send_system_alert(
                component="backup_service",
                description=f"Backup failed: {str(e)}",
                severity="error",
                details={
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
            raise
    
    def restore_backup(self, backup_path, validate_first=True):
        """Restore the database from a backup.
        
        Args:
            backup_path: Path to the backup file
            validate_first: Whether to validate the backup before restoring
            
        Returns:
            bool: True if restore was successful
        """
        try:
            backup_path = Path(backup_path)
            if not backup_path.exists():
                raise FileNotFoundError(f"Backup file not found: {backup_path}")
            
            # Validate backup if requested
            if validate_first:
                if not self.validate_backup(backup_path):
                    raise ValueError("Backup validation failed")
            
            # For safety, create a backup of current database before restoring
            current_backup = self.create_backup()
            logger.info(f"Created safety backup before restore: {current_backup}")
            
            # Handle compressed backups
            if backup_path.suffix == '.gz':
                import gzip
                temp_path = backup_path.with_suffix('')
                with gzip.open(backup_path, 'rb') as f_in:
                    with open(temp_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                backup_path = temp_path
                # Note: We don't delete the compressed file as we might need it again
            
            # Close any existing connections (in a real app, you'd need to ensure no active connections)
            # For SQLite, we just copy the file
            shutil.copy2(backup_path, self.db_path)
            logger.info(f"Database restored from backup: {backup_path}")
            
            # Send success alert
            send_system_alert(
                component="backup_service",
                description=f"Database restored from backup: {backup_path.name}",
                severity="info",
                details={
                    "backup_path": str(backup_path)
                }
            )
            
            # Clean up temporary file if we created one
            if backup_path.suffix == '' and backup_path.name != self.db_path.name:
                backup_path.unlink()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")
            
            # Send failure alert
            send_system_alert(
                component="backup_service",
                description=f"Restore failed: {str(e)}",
                severity="error",
                details={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "backup_path": str(backup_path) if 'backup_path' in locals() else None
                }
            )
            raise
    
    def validate_backup(self, backup_path):
        """Validate a backup file by attempting to open it and check basic integrity.
        
        Args:
            backup_path: Path to the backup file
            
        Returns:
            bool: True if backup appears valid
        """
        try:
            backup_path = Path(backup_path)
            if not backup_path.exists():
                return False
            
            # Handle compressed backups
            if backup_path.suffix == '.gz':
                import gzip
                with gzip.open(backup_path, 'rb') as f:
                    # Read a small portion to verify it's a valid gzip file
                    header = f.read(10)
                    if not header:
                        return False
            else:
                # For uncompressed files, try to open as SQLite database
                conn = sqlite3.connect(str(backup_path))
                cursor = conn.cursor()
                # Try to read from sqlite_master to verify it's a valid SQLite database
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
                result = cursor.fetchone()
                conn.close()
                if not result:
                    return False
            
            logger.info(f"Backup validation passed: {backup_path}")
            return True
            
        except Exception as e:
            logger.warning(f"Backup validation failed for {backup_path}: {e}")
            return False
    
    def cleanup_old_backups(self):
        """Remove backup files older than the retention period."""
        try:
            cutoff_date = datetime.now() - timedelta(days=self.backup_retention_days)
            deleted_count = 0
            
            for backup_file in self.backup_dir.glob("solenos_backup_*.db"):
                try:
                    # Extract timestamp from filename
                    # Format: solenos_backup_YYYYMMDD_HHMMSS.db
                    filename = backup_file.name
                    if filename.startswith("solenos_backup_") and filename.endswith('.db'):
                        # Extract the timestamp part
                        timestamp_str = filename[15:-3]  # Remove "solenos_backup_" and ".db"
                        if len(timestamp_str) == 12:  # YYYYMMDD_HHMMSS
                            file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                            if file_date < cutoff_date:
                                backup_file.unlink()
                                deleted_count += 1
                                logger.info(f"Deleted old backup: {backup_file}")
                except Exception as e:
                    logger.warning(f"Could not process backup file {backup_file}: {e}")
            
            # Also handle compressed backups
            for backup_file in self.backup_dir.glob("solenos_backup_*.db.gz"):
                try:
                    # Extract timestamp from filename
                    # Format: solenos_backup_YYYYMMDD_HHMMSS.db.gz
                    filename = backup_file.name
                    if filename.startswith("solenos_backup_") and filename.endswith('.db.gz'):
                        # Extract the timestamp part
                        timestamp_str = filename[15:-7]  # Remove "solenos_backup_" and ".db.gz"
                        if len(timestamp_str) == 12:  # YYYYMMDD_HHMMSS
                            file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                            if file_date < cutoff_date:
                                backup_file.unlink()
                                deleted_count += 1
                                logger.info(f"Deleted old backup: {backup_file}")
                except Exception as e:
                    logger.warning(f"Could not process backup file {backup_file}: {e}")
            
            if deleted_count > 0:
                logger.info(f"Cleanup complete. Deleted {deleted_count} old backups.")
            else:
                logger.info("No old backups to clean up.")
                
        except Exception as e:
            logger.error(f"Error during backup cleanup: {e}")
            
            # Send failure alert
            send_system_alert(
                component="backup_service",
                description=f"Backup cleanup failed: {str(e)}",
                severity="warning",
                details={
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
    
    def list_backups(self):
        """List all available backups, sorted by date (newest first).
        
        Returns:
            list: List of backup file information dictionaries
        """
        try:
            backups = []
            for backup_file in self.backup_dir.glob("solenos_backup_*.db"):
                try:
                    stat = backup_file.stat()
                    # Extract timestamp from filename
                    filename = backup_file.name
                    if filename.startswith("solenos_backup_") and filename.endswith('.db'):
                        timestamp_str = filename[15:-3]  # Remove "solenos_backup_" and ".db"
                        if len(timestamp_str) == 12:
                            try:
                                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                                backups.append({
                                    'path': str(backup_file),
                                    'name': backup_file.name,
                                    'size': stat.st_size,
                                    'created_at': timestamp,
                                    'is_compressed': False
                                })
                            except ValueError:
                                # If timestamp parsing fails, still include the file
                                backups.append({
                                    'path': str(backup_file),
                                    'name': backup_file.name,
                                    'size': stat.st_size,
                                    'created_at': datetime.fromtimestamp(stat.st_mtime),
                                    'is_compressed': False
                                })
                except Exception as e:
                    logger.warning(f"Could not process backup file {backup_file}: {e}")
            
            # Also handle compressed backups
            for backup_file in self.backup_dir.glob("solenos_backup_*.db.gz"):
                try:
                    stat = backup_file.stat()
                    # Extract timestamp from filename
                    filename = backup_file.name
                    if filename.startswith("solenos_backup_") and filename.endswith('.db.gz'):
                        timestamp_str = filename[15:-7]  # Remove "solenos_backup_" and ".db.gz"
                        if len(timestamp_str) == 12:
                            try:
                                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                                backups.append({
                                    'path': str(backup_file),
                                    'name': backup_file.name,
                                    'size': stat.st_size,
                                    'created_at': timestamp,
                                    'is_compressed': True
                                })
                            except ValueError:
                                # If timestamp parsing fails, still include the file
                                backups.append({
                                    'path': str(backup_file),
                                    'name': backup_file.name,
                                    'size': stat.st_size,
                                    'created_at': datetime.fromtimestamp(stat.st_mtime),
                                    'is_compressed': True
                                })
                except Exception as e:
                    logger.warning(f"Could not process backup file {backup_file}: {e}")
            
            # Sort by creation date, newest first
            backups.sort(key=lambda x: x['created_at'], reverse=True)
            return backups
            
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            
            # Send failure alert
            send_system_alert(
                component="backup_service",
                description=f"Failed to list backups: {str(e)}",
                severity="warning",
                details={
                    "error": str(e),
                    "error_type": type(e).__name__
                }
            )
            return []

# Global instance for easy access
backup_service = BackupService()

# Convenience functions
def create_backup():
    """Convenience function to create a backup."""
    return backup_service.create_backup()

def restore_backup(backup_path):
    """Convenience function to restore a backup."""
    return backup_service.restore_backup(backup_path)

def cleanup_old_backups():
    """Convenience function to clean up old backups."""
    backup_service.cleanup_old_backups()

def list_backups():
    """Convenience function to list backups."""
    return backup_service.list_backups()

