"""Custom exceptions for the execution module."""


class ExecutionError(Exception):
    """Base exception for execution errors."""


class ContainerError(ExecutionError):
    """Raised for errors related to container management."""


class AgentError(ExecutionError):
    """Raised for errors related to the coding agent."""
