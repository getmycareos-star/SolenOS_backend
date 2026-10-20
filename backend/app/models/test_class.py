class TestClass(Base):
    """Test class"""
    __tablename__ = "test"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
