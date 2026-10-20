"""Alerting service for SolenOS.

Provides mechanisms for sending alerts about security incidents,
system failures, and other important events.
"""

import json
import logging
import smtplib
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List, Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def send_alert(subject: str, message: str, alert_type: str = "info", 
               details: Optional[Dict[str, Any]] = None) -> bool:
    """
    Send an alert via configured channels.
    
    Args:
        subject: Alert subject/title
        message: Alert message body
        alert_type: Type of alert (info, warning, error, critical)
        details: Additional details to include in the alert
        
    Returns:
        bool: True if alert was sent successfully, False otherwise
    """
    if not settings.alerting_enabled:
        logger.debug("Alerting is disabled, not sending alert")
        return False
    
    success = True
    
    # Send via webhook if configured
    if settings.alert_webhook_url:
        if not _send_webhook_alert(subject, message, alert_type, details):
            success = False
    
    # Send via email if configured
    if settings.alert_email_enabled:
        if not _send_email_alert(subject, message, alert_type, details):
            success = False
    
    return success


def _send_webhook_alert(subject: str, message: str, alert_type: str, 
                        details: Optional[Dict[str, Any]]) -> bool:
    """Send alert via webhook (e.g., Slack, Discord, etc.)."""
    try:
        # Prepare payload for webhook
        payload = {
            "subject": subject,
            "message": message,
            "type": alert_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {}
        }
        
        # Convert to JSON
        data = json.dumps(payload).encode('utf-8')
        
        # Create request
        req = urllib.request.Request(
            settings.alert_webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        
        # Send request
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.getcode() == 200:
                logger.info(f"Alert sent via webhook: {subject}")
                return True
            else:
                logger.error(f"Webhook returned status {response.getcode()}")
                return False
                
    except Exception as e:
        logger.error(f"Failed to send webhook alert: {e}")
        return False


def _send_email_alert(subject: str, message: str, alert_type: str, 
                      details: Optional[Dict[str, Any]]) -> bool:
    """Send alert via email."""
    try:
        # Check if email settings are configured
        if not all([settings.alert_email_smtp_server, 
                   settings.alert_email_username, 
                   settings.alert_email_password,
                   settings.alert_email_from]):
            logger.error("Email alerting not fully configured")
            return False
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = settings.alert_email_from
        msg['To'] = ", ".join(settings.alert_email_to)
        msg['Subject'] = f"[{alert_type.upper()}] {subject}"
        
        # Build email body
        body = f"""
Alert Type: {alert_type.upper()}
Subject: {subject}
Message: {message}
Timestamp: {datetime.now(timezone.utc).isoformat()}
"""
        
        if details:
            body += f"\nDetails:\n{json.dumps(details, indent=2)}"
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Send email
        server = smtplib.SMTP(settings.alert_email_smtp_server, settings.alert_email_smtp_port)
        server.starttls()
        server.login(settings.alert_email_username, settings.alert_email_password)
        text = msg.as_string()
        server.sendmail(settings.alert_email_from, settings.alert_email_to, text)
        server.quit()
        
        logger.info(f"Alert sent via email: {subject}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email alert: {e}")
        return False


def send_security_alert(event_type: str, description: str, 
                       severity: str = "warning", 
                       source_ip: Optional[str] = None,
                       user_id: Optional[str] = None,
                       details: Optional[Dict[str, Any]] = None) -> bool:
    """
    Send a security-specific alert.
    
    Args:
        event_type: Type of security event (e.g., "failed_login", "malware_detected")
        description: Description of the event
        severity: Severity level (info, warning, error, critical)
        source_ip: Source IP address if applicable
        user_id: User ID if applicable
        details: Additional details
        
    Returns:
        bool: True if alert was sent successfully
    """
    alert_details = {
        "event_type": event_type,
        "source_ip": source_ip,
        "user_id": user_id,
        **(details or {})
    }
    
    subject = f"Security Alert: {event_type}"
    message = f"Security event detected: {description}"
    
    return send_alert(subject, message, severity, alert_details)


def send_system_alert(component: str, description: str, 
                     severity: str = "warning",
                     details: Optional[Dict[str, Any]] = None) -> bool:
    """
    Send a system-related alert.
    
    Args:
        component: System component (e.g., "database", "backup_service")
        description: Description of the issue
        severity: Severity level (info, warning, error, critical)
        details: Additional details
        
    Returns:
        bool: True if alert was sent successfully
    """
    alert_details = {
        "component": component,
        **(details or {})
    }
    
    subject = f"System Alert: {component}"
    message = f"System issue detected: {description}"
    
    return send_alert(subject, message, severity, alert_details)