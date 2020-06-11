# vim: sw=4 ts=4 et

# linked_list: Simple doubly linked list with dictionary pointing to
# first occurrence of the element
# Copyright (C) 2017 jchtt

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

class DLNode:
    """
    Container class for an element of a doubly linked list
    """

    def __init__(self, content, prev = None, next = None):
        """
        Construct with key value key, content object content, and pointers to
        the previous and next element, defaulting to None.
        """
        # self.key = key
        self.content = content
        self.prev = prev
        self.next = next
        self.removed = False

    def remove(self):
        """
        Remove node by unlinking it.
        """
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
        self.removed = True

    def next_elements(self, include = False):
        """
        Generator that yields the following elements, including the element itself if
        include is True.
        """
        if not include:
            current_node = self.next
        else:
            current_node = self
        while current_node is not None:
            yield current_node
            current_node = current_node.next

    def prev_elements(self, include = False):
        """
        Generator that yields the preceding elements, including the element itself if
        include is True.
        """
        if not include:
            current_node = self.prev
        else:
            current_node = self
        while current_node is not None:
            yield current_node
            current_node = current_node.prev

    def __str__(self):
        return(str(self.content))


class DLList:
    """
    Doubly linked list with transparent wrapper class DLNode.
    Each element needs to have a unique key and is accessible in O(1)
    by that key.
    """

    def __init__(self):
        """
        Construct empty list.
        """
        self.head = None
        self.tail = None
        # self.key_dict = {}

    def __str__(self):
        """
        Simple string representation by parsing it into a list.
        """
        l = []
        node = self.head
        while node is not None:
            l.append(node)
            node = node.next

        # return "DLList([{}])".format(", ".join(["{}: {}".format(node.key, str(node.content)) for node in l]))
        return "DLList([{}])".format(", ".join(["{}".format(str(node.content)) for node in l]))

    def __iter__(self):
        """
        Iterator that starts at the head and goes from there.
        """
        if self.head is not None:
            return self.head.next_elements(include = True)
        else:
            return iter(())

    def append(self, content):
        """
        Add a new node by key and content.
        """
        node = DLNode(content, self.tail, None)
        if self.tail is not None:
            self.tail.next = node
        else:
            self.head = node
        self.tail = node
        # if key not in self.key_dict:
        #     self.key_dict[key] = node
        return node
        
    # Deprecated
    # def remove(self, key):
    #     """
    #     Remove a node by key.
    #     """
    #     node = self.key_dict[key]
    #     if node is self.head:
    #         self.head = node.next
    #     if node is self.tail:
    #         self.tail = node.prev
    #     node.remove()
    #     del self.key_dict[key]

    def remove(self, node):
        """
        Remove a node
        """
        if node is self.head:
            self.head = node.next
        if node is self.tail:
            self.tail = node.prev
        node.remove()
        # del self.key_dict[key]

    def isempty(self):
        """
        Tell if the list is empty.
        """
        return self.head is None
