# gRPC and Protocol Buffers

gRPC is a high-performance remote procedure call framework. Service methods and
message types are declared in a protocol buffer schema, and the compiler generates
client and server stubs in many languages. Messages serialize to a compact binary
format that is smaller and faster to parse than JSON. gRPC runs over HTTP/2, enabling
bidirectional streaming where client and server send sequences of messages over a
single long-lived connection.
