from __future__ import annotations

from pyifc.core.models import (
    ApplicationSpecification,
    CapabilityDeclaration,
    ComponentDeclaration,
    ResourceDeclaration,
)


class DeclarationError(ValueError):
    pass


class ApplicationRegistry:
    def __init__(self, application_name: str) -> None:
        self.application_name = application_name

        self._resources: dict[str, ResourceDeclaration] = {}
        self._components: dict[str, ComponentDeclaration] = {}
        self._capabilities: list[CapabilityDeclaration] = []

    def register_resource(
        self,
        resource: ResourceDeclaration,
    ) -> None:
        existing = self._resources.get(resource.id)

        if existing is not None and existing != resource:
            raise DeclarationError(
                f"Resource '{resource.id}' was declared more than once "
                "with different definitions."
            )

        self._resources[resource.id] = resource

    def register_component(
        self,
        component: ComponentDeclaration,
    ) -> None:
        existing = self._components.get(component.id)

        if existing is not None:
            if existing.kind != component.kind:
                raise DeclarationError(
                    f"Component '{component.id}' was registered as both "
                    f"'{existing.kind}' and '{component.kind}'."
                )

            if existing.callable_ref != component.callable_ref:
                raise DeclarationError(
                    f"Component ID '{component.id}' is already used by "
                    f"'{existing.callable_ref}'."
                )

            existing.properties.update(component.properties)

            if existing.source is None:
                existing.source = component.source

            return

        self._components[component.id] = component

    def rename_capability_component(
        self,
        old_component_id: str,
        new_component_id: str,
    ) -> None:
        self._capabilities = [
            CapabilityDeclaration(
                component_id=new_component_id,
                resource_id=capability.resource_id,
                operation=capability.operation,
                source=capability.source,
                provenance=capability.provenance,
            )
            if capability.component_id == old_component_id
            else capability
            for capability in self._capabilities
        ]

    def register_capability(
        self,
        capability: CapabilityDeclaration,
    ) -> None:
        if capability not in self._capabilities:
            self._capabilities.append(capability)

    def to_specification(self) -> ApplicationSpecification:
        components = [
            ComponentDeclaration(
                id=component.id,
                kind=component.kind,
                callable_ref=component.callable_ref,
                properties=dict(component.properties),
                source=component.source,
                trigger=component.trigger or self._component_trigger(component.id),
            )
            for component in self._components.values()
        ]
        return ApplicationSpecification(
            name=self.application_name,
            resources=sorted(
                self._resources.values(),
                key=lambda resource: resource.id,
            ),
            components=sorted(components, key=lambda component: component.id),
            capabilities=sorted(
                self._capabilities,
                key=lambda capability: (
                    capability.component_id,
                    capability.resource_id,
                    capability.operation,
                ),
            ),
        )

    def _component_trigger(self, component_id: str) -> str | None:
        for capability in self._capabilities:
            if capability.component_id != component_id:
                continue
            if capability.operation.value != "subscribe":
                continue
            resource = self._resources.get(capability.resource_id)
            if resource is None:
                continue
            trigger_kind = "topic" if resource.kind.value == "topic" else "queue"
            return f"{trigger_kind}:{resource.id}"
        return None