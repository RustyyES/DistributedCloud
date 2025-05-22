class DistributedCloudError(Exception):
    """Base exception for DistributedCloud"""
    pass

class NodeNotFoundError(DistributedCloudError):
    """Raised when a node cannot be found in the registry"""
    pass

class JobNotFoundError(DistributedCloudError):
    """Raised when a job cannot be found"""
    pass

class SSHConnectionError(DistributedCloudError):
    """Raised when SSH connection fails"""
    pass

class ResourceUnavailableError(DistributedCloudError):
    """Raised when resources are insufficient for a job"""
    pass
