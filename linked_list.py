class DLNode:
    def __init__(self, key, content, prev = None, next = None):
        self.key = key
        self.content = content
        self.prev = prev
        self.next = next

    def remove(self):
        if self.next is None:
            if self.prev is not None:
                # End of chain
                self.prev.next = None
        elif self.prev is None:
            if self.next is not None:
                # Beginning of chain
                self.next.prev = None
        else:
            self.next.prev = self.prev
            self.prev.next = self.next


class DLList:
    def __init__(self):
        self.head = None
        self.tail = None
        self.key_dict = {}

    def __str__(self):
        l = []
        node = self.head
        while node is not None:
            l.append(node)
            node = node.next

        return "DLList([{}])".format(", ".join(["{}: {}".format(node.key, str(node.content)) for node in l]))

    def append(self, key, content):
        node = DLNode(key, content, self.tail, None)
        if self.tail is not None:
            self.tail.next = node
        else:
            self.head = node
        self.tail = node
        self.key_dict[key] = node
        
    def remove(self, key):
        node = self.key_dict[key]
        if node is self.head:
            self.head = node.next
        if node is self.tail:
            self.tail = node.prev
        node.remove()
        del self.key_dict[key]
